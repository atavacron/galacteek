import sys
import os
import os.path
import uuid
import logging
import asyncio
import pkg_resources
import jinja2
import jinja2.exceptions
import warnings
import concurrent.futures
import re
import platform
import async_timeout
import time
import aiojobs
import shutil
from pathlib import Path
from filelock import FileLock

from distutils.version import StrictVersion

from quamash import QEventLoop

from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QDesktopWidget
from PyQt5.QtWidgets import QSystemTrayIcon
from PyQt5.QtWidgets import QMenu

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtCore import QUrl
from PyQt5.QtCore import QStandardPaths
from PyQt5.QtCore import QTranslator
from PyQt5.QtCore import QFile
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import QObject
from PyQt5.QtCore import QTemporaryDir
from PyQt5.QtCore import QDir
from PyQt5.QtCore import QMimeDatabase

from PyQt5.QtGui import QCursor

from galacteek import log
from galacteek import logUser
from galacteek import ensure
from galacteek import AsyncSignal
from galacteek import pypicheck, GALACTEEK_NAME

from galacteek.core.asynclib import asyncify
from galacteek.core.asynclib import cancelAllTasks
from galacteek.core.ctx import IPFSContext
from galacteek.core.multihashmetadb import IPFSObjectMetadataDatabase
from galacteek.core.clipboard import ClipboardTracker
from galacteek.core.db import SqliteDatabase

from galacteek import database
from galacteek.database import models

from galacteek.hashmarks import HashmarksSynchronizer

from galacteek.core.models.atomfeeds import AtomFeedsModel
from galacteek.core.signaltowers import DAGSignalsTower
from galacteek.core.signaltowers import URLSchemesTower
from galacteek.core.signaltowers import DIDTower
from galacteek.core.analyzer import ResourceAnalyzer

from galacteek.core.schemes import SCHEME_MANUAL
from galacteek.core.schemes import DWebSchemeHandlerGateway
from galacteek.core.schemes import EthDNSSchemeHandler
from galacteek.core.schemes import EthDNSProxySchemeHandler
from galacteek.core.schemes import NativeIPFSSchemeHandler
from galacteek.core.schemes import ObjectProxySchemeHandler
from galacteek.core.schemes import MultiObjectHostSchemeHandler

from galacteek.dweb.ethereum import EthereumConnectionParams
from galacteek.dweb.ethereum import MockEthereumController

from galacteek.space.solarsystem import SolarSystem

from galacteek.ipfs import asyncipfsd, cidhelpers
from galacteek.ipfs.cidhelpers import joinIpfs
from galacteek.ipfs.cidhelpers import IPFSPath
from galacteek.ipfs.ipfsops import *
from galacteek.ipfs.wrappers import *
from galacteek.ipfs.feeds import FeedFollower
from galacteek.ipfs import distipfsfetch

from galacteek.did.ipid import IPIDManager

from galacteek.ipdapps.loader import DappsRegistry

from galacteek.core.webprofiles import IPFSProfile
from galacteek.core.webprofiles import Web3Profile
from galacteek.core.webprofiles import MinimalProfile

from galacteek.dweb.webscripts import ipfsClientScripts
from galacteek.dweb.render import defaultJinjaEnv

from galacteek.ui import mainui
from galacteek.ui import downloads
from galacteek.ui import peers
from galacteek.ui import history
from galacteek.ui.resource import IPFSResourceOpener
from galacteek.ui.style import GalacteekStyle

from galacteek.ui.helpers import *
from galacteek.ui.i18n import *

from galacteek.appsettings import *
from galacteek.core.ipfsmarks import IPFSMarks

from yarl import URL

import aioipfs

# IPFS daemon messages


def iIpfsDaemonStarted():
    return QCoreApplication.translate('Galacteek', 'IPFS daemon started')


def iIpfsDaemonGwStarted():
    return QCoreApplication.translate('Galacteek',
                                      "IPFS daemon's gateway started")


def iIpfsDaemonReady():
    return QCoreApplication.translate('Galacteek', 'IPFS daemon is ready')


def iIpfsDaemonProblem():
    return QCoreApplication.translate('Galacteek',
                                      'Problem starting IPFS daemon')


def iIpfsDaemonInitProblem():
    return QCoreApplication.translate(
        'Galacteek',
        'Problem initializing the IPFS daemon (check the ports configuration)')


def iIpfsDaemonWaiting(count):
    return QCoreApplication.translate(
        'Galacteek',
        'IPFS daemon: waiting for connection (try {0})'.format(count))


def ipfsVersion():
    try:
        p = subprocess.Popen(['ipfs', 'version', '-n'], stdout=subprocess.PIPE)
        out, err = p.communicate()
        version = out.decode().strip()
        version = re.sub('(-.*$)', '', version)
        return StrictVersion(version)
    except BaseException:
        return None


async def fetchGoIpfsWrapper(app, timeout=60 * 10):
    try:
        await asyncio.wait_for(fetchIpfsSoft(
            app, 'go-ipfs', 'ipfs', '0.5.0'), timeout)
    except asyncio.TimeoutError:
        app.mainWindow.statusMessage(iGoIpfsFetchTimeout())
        return None
    except Exception:
        app.mainWindow.statusMessage(iGoIpfsFetchError())
        return None
    else:
        app.mainWindow.statusMessage(iGoIpfsFetchSuccess())
        return app.which('ipfs')


async def fetchFsMigrateWrapper(app, timeout=60 * 10):
    try:
        await asyncio.wait_for(fetchIpfsSoft(
            app, 'fs-repo-migrations', 'fs-repo-migrations',
            '1.5.1'), timeout)
    except asyncio.TimeoutError:
        app.mainWindow.statusMessage(iGoIpfsFetchTimeout())
        return None
    except Exception:
        app.mainWindow.statusMessage(iGoIpfsFetchError())
        return None
    else:
        app.mainWindow.statusMessage(iGoIpfsFetchSuccess())
        return app.which('fs-repo-migrations')


async def fetchIpfsSoft(app, software, executable, version):
    async for msg in distipfsfetch.distIpfsExtract(
            dstdir=app.ipfsBinLocation, software=software,
            executable=executable, version=version, loop=app.loop,
            sslverify=app.sslverify):
        try:
            code, text = msg
            app.mainWindow.statusMessage(text)
        except Exception as e:
            app.debug(str(e))


class IPFSConnectionParams(object):
    def __init__(self, host, apiport, gwport):
        self._host = host
        self._apiPort = apiport
        self._gatewayPort = gwport

        self._gatewayUrl = URL.build(
            host=self.host,
            port=self.gatewayPort,
            scheme='http',
            path='')

    @property
    def host(self):
        return self._host

    @property
    def apiPort(self):
        return self._apiPort

    @property
    def gatewayPort(self):
        return self._gatewayPort

    @property
    def gatewayUrl(self):
        return self._gatewayUrl


class GalacteekApplication(QApplication):
    """
    Galacteek application class

    :param bool debug: enable debugging
    :param str profile: application profile
    """

    manualAvailable = pyqtSignal(str, dict)
    messageDisplayRequest = pyqtSignal(str, str)
    appImageImported = pyqtSignal(str)

    dbConfigured = AsyncSignal(bool)

    def __init__(self, debug=False, profile='main', sslverify=True,
                 enableOrbital=False, progName=None, cmdArgs={}):
        QApplication.__init__(self, sys.argv)

        QCoreApplication.setApplicationName(GALACTEEK_NAME)

        self.dbConfigured.connectTo(self.onDbConfigured)

        self.setQuitOnLastWindowClosed(False)

        self._cmdArgs = cmdArgs
        self._debugEnabled = debug
        self._appProfile = profile
        self._loop = None
        self._executor = None
        self._ipfsClient = None
        self._ipfsOpMain = None
        self._ipfsd = None
        self._sslverify = sslverify
        self._progName = progName
        self._progCid = None
        self._system = platform.system()
        self._urlSchemes = {}
        self._shuttingDown = False

        self._icons = {}
        self._ipfsIconsCache = {}
        self._ipfsIconsCacheMax = 32

        self.enableOrbital = enableOrbital
        self.orbitConnector = None

        self.translator = None
        self.mainWindow = None
        self.feedFollowerTask = None

        self.webProfiles = {}
        self.ipfsCtx = IPFSContext(self)
        self.peersTracker = peers.PeersTracker(self.ipfsCtx)

        self.desktopWidget = QDesktopWidget()
        self.desktopGeometry = self.desktopWidget.screenGeometry()

        self.setWindowIcon(getIcon('galacteek.png'))

        self.setupAsyncLoop()
        self.setupPaths()

    @property
    def cmdArgs(self):
        return self._cmdArgs

    @property
    def shuttingDown(self):
        return self._shuttingDown

    @property
    def offline(self):
        return self.cmdArgs.offline

    @property
    def system(self):
        return self._system

    @property
    def debugEnabled(self):
        return self._debugEnabled

    @property
    def ipfsIconsCacheMax(self):
        return self._ipfsIconsCacheMax

    @property
    def ipfsIconsCache(self):
        return self._ipfsIconsCache

    @property
    def progName(self):
        return self._progName

    @property
    def progCid(self):
        return self._progCid

    @property
    def sslverify(self):
        return self._sslverify

    @property
    def appProfile(self):
        return self._appProfile

    @property
    def ipfsd(self):
        return self._ipfsd

    @property
    def loop(self):
        return self._loop

    @property
    def executor(self):
        return self._executor

    @loop.setter
    def loop(self, newLoop):
        self._loop = newLoop

    @property
    def allTasks(self):
        return asyncio.Task.all_tasks(loop=self.loop)

    @property
    def pendingTasks(self):
        return [task for task in self.allTasks if not task.done()]

    @property
    def ipfsClient(self):
        return self._ipfsClient

    @ipfsClient.setter
    def ipfsClient(self, client):
        self.debug('IPFS client changed: {}'.format(client))
        self._ipfsClient = client

    @property
    def ipfsOpMain(self):
        return self._ipfsOpMain

    @ipfsOpMain.setter
    def ipfsOpMain(self, op):
        """ The main IPFS operator, used by @ipfsOp """
        self.debug('Main IPFS operator upgrade: ID {}'.format(op.uid))
        self._ipfsOpMain = op

    @property
    def gatewayAuthority(self):
        params = self.getIpfsConnectionParams()
        return '{0}:{1}'.format(params.host, params.gatewayPort)

    @property
    def gatewayUrl(self):
        params = self.getIpfsConnectionParams()
        return params.gatewayUrl

    @property
    def dataLocation(self):
        return self._dataLocation

    @property
    def ipfsBinLocation(self):
        return self._ipfsBinLocation

    @property
    def ipfsDataLocation(self):
        return self._ipfsDataLocation

    @property
    def nsCacheLocation(self):
        return self._nsCacheLocation

    @property
    def orbitDataLocation(self):
        return self._orbitDataLocation

    def applyStyle(self, theme='default'):
        qssPath = ":/share/static/qss/{theme}/galacteek.qss".format(
            theme=theme)
        qssFile = QFile(qssPath)

        try:
            qssFile.open(QFile.ReadOnly)
            styleSheetBa = qssFile.readAll()
            styleSheetStr = styleSheetBa.data().decode('utf-8')
            self.setStyleSheet(styleSheetStr)
        except BaseException:
            # that would probably occur if the QSS is not
            # in the resources file..  set some default stylesheet here?
            pass

        self.gStyle = GalacteekStyle()
        self.setStyle(self.gStyle)

    def debug(self, msg):
        if self.debugEnabled:
            log.debug(msg)

    def initSystemTray(self):
        self.systemTray = QSystemTrayIcon(self)
        self.systemTray.setIcon(getIcon('galacteek-incandescent.png'))
        self.systemTray.show()
        self.systemTray.activated.connect(self.onSystemTrayIconClicked)

        systemTrayMenu = QMenu(self.mainWindow)

        actionShow = systemTrayMenu.addAction('Show')
        actionShow.setIcon(getIcon('galacteek-incandescent.png'))
        actionShow.triggered.connect(self.onShowWindow)

        systemTrayMenu.addSeparator()

        actionQuit = systemTrayMenu.addAction('Quit')
        actionQuit.setIcon(getIcon('quit.png'))
        actionQuit.triggered.connect(self.onExit)

        self.systemTray.setContextMenu(systemTrayMenu)

    def initMisc(self):
        self.multihashDb = IPFSObjectMetadataDatabase(self._mHashDbLocation,
                                                      loop=self.loop)

        self.resourceOpener = IPFSResourceOpener(parent=self)

        self.downloadsManager = downloads.DownloadsManager(self)
        self.marksLocal = IPFSMarks(self.localMarksFileLocation, backup=True)

        self.marksLocal.addCategory('general')
        self.marksLocal.addCategory('uncategorized')
        self.marksNetwork = IPFSMarks(self.networkMarksFileLocation,
                                      autosave=False)

        self.tempDir = QTemporaryDir()
        self.tempDirWeb = self.tempDirCreate(
            self.tempDir.path(), 'webdownloads')

    def tempDirCreate(self, basedir, name=None):
        tmpdir = QDir(basedir)

        if not tmpdir.exists():
            return

        uid = name if name else str(uuid.uuid4())

        path = tmpdir.absoluteFilePath(uid)
        if tmpdir.mkpath(path):
            return path

    async def setupHashmarks(self):
        pkg = 'galacteek.hashmarks.default'

        res = await database.hashmarkSourceSearch(
            name='core',
            url=pkg,
            type=models.HashmarkSource.TYPE_PYMODULE
        )

        if not res:
            await database.hashmarkSourceAdd(
                type=models.HashmarkSource.TYPE_PYMODULE,
                url=pkg,
                name='core'
            )
            await self.hmSynchronizer.sync()

        await database.hashmarkSourceAdd(
            type=models.HashmarkSource.TYPE_GITREPOS,
            url='https://github.com/galacteek/hashmarks-dwebland'
        )

        await self.scheduler.spawn(self.hmSynchronizer.syncTask())

    def setupTranslator(self):
        if self.translator:
            QApplication.removeTranslator(self.translator)

        self.translator = QTranslator()
        QApplication.installTranslator(self.translator)
        lang = self.settingsMgr.getSetting(CFG_SECTION_UI, CFG_KEY_LANG)
        self.translator.load(':/share/translations/galacteek_{0}.qm'.format(
            lang))

    def createMainWindow(self, show=True):
        self.mainWindow = mainui.MainWindow(self)
        if show is True:
            self.mainWindow.show()

    def onSystemTrayIconClicked(self, reason):
        if reason == QSystemTrayIcon.Unknown:
            pass
        elif reason == QSystemTrayIcon.Context:
            pass
        elif reason == QSystemTrayIcon.DoubleClick:
            self.mainWindow.showMaximized()
        else:
            pass

    def systemTrayMessage(self, title, message, timeout=2000,
                          messageIcon=QSystemTrayIcon.Information):
        self.systemTray.showMessage(title, message, messageIcon, timeout)

    @ipfsOp
    async def setupRepository(self, op):
        pubsubEnabled = True  # mandatory now ..
        hExchEnabled = self.settingsMgr.isTrue(CFG_SECTION_IPFS,
                                               CFG_KEY_HASHMARKSEXCH)

        self.ipfsCtx.resources['ipfs-logo-ice'] = await self.importQtResource(
            '/share/icons/ipfs-logo-128-ice.png')
        self.ipfsCtx.resources['ipfs-cube-64'] = await self.importQtResource(
            '/share/icons/ipfs-cube-64.png')
        self.ipfsCtx.resources['atom-feed'] = await self.importQtResource(
            '/share/icons/atom-feed.png')
        self.ipfsCtx.resources['markdown-reference'] = \
            await self.importQtResource(
                '/share/static/docs/markdown-reference.html')

        await self.ipfsCtx.setup(pubsubEnable=pubsubEnabled,
                                 pubsubHashmarksExch=hExchEnabled,
                                 offline=self.offline)
        await self.ipfsCtx.profilesInit()
        await self.qSchemeHandler.start()
        await self.importLdContexts()

        self.feedFollower = FeedFollower(self)
        self.feedFollowerTask = await self.scheduler.spawn(
            self.feedFollower.process())

        await self.ipfsCtx.ipfsRepositoryReady.emit()
        self.ipfsCtx._ipfsRepositoryReady.emit()

        #
        # If the application's binary name is a valid CID, pin it!
        # This happens when running the AppImage and ensures
        # self-seeding of the image!
        #

        if isinstance(self.progName, str):
            progNameClean = re.sub(r'[\.\/]*', '', self.progName)
            if cidhelpers.cidValid(progNameClean):
                self._progCid = progNameClean
                log.debug("Auto pinning program's CID: {0}".format(
                    self.progCid))
                await self.ipfsCtx.pin(joinIpfs(self.progCid), False,
                                       self.onAppReplication,
                                       qname='self-seeding')

        if self.cmdArgs.seed and self.cmdArgs.appimage:
            await self.seedAppImage()

    @ipfsOp
    async def seedAppImage(self, ipfsop):
        # Automatic AppImage seeding

        if os.path.isfile(self.cmdArgs.binarypath):
            log.info('AppImage seeding: {img}'.format(
                img=self.cmdArgs.binarypath
            ))

            ensure(ipfsop.addPath(
                self.cmdArgs.binarypath,
                wrap=True
            ), futcallback=self.onAppSeed)

    def onAppSeed(self, future):
        try:
            replResult = future.result()
        except Exception as err:
            log.debug('AppImage seed: failed', exc_info=err)
        else:
            if isinstance(replResult, dict):
                cid = replResult.get('Hash')

                self.appImageImported.emit(cid)

                log.info('AppImage seed OK: CID {cid}'.format(
                    cid=cid))

    def onAppReplication(self, future):
        try:
            replResult = future.result()
        except Exception as err:
            log.debug('App replication: failed', exc_info=err)
        else:
            log.debug('App replication: success ({result})'.format(
                result=replResult))

    @ipfsOp
    async def importLdContexts(self, ipfsop):
        """
        Import the JSON-LD contexts and associate the
        directory entry with the 'galacteek.ld.contexts' key
        """

        contextsPath = ipfsop.ldContextsRootPath()

        if not os.path.isdir(contextsPath):
            log.debug('LD contexts not found')
            return

        entry = await ipfsop.addPath(
            contextsPath, recursive=True,
            hidden=False
        )
        if entry:
            ldKeyName = 'galacteek.ld.contexts'
            log.debug('LD contexts sitting at: {}'.format(
                entry.get('Hash')))
            await ipfsop.keyGen(
                ldKeyName,
                checkExisting=True
            )
            ensure(ipfsop.publish(
                entry['Hash'],
                key=ldKeyName,
                allow_offline=True
            ))

    @ipfsOp
    async def importQtResource(self, op, path):
        rscFile = QFile(':{0}'.format(path))

        try:
            rscFile.open(QFile.ReadOnly)
            data = rscFile.readAll().data()
            entry = await op.addBytes(data)
        except Exception as e:
            log.debug('importQtResource: {}'.format(str(e)))
        else:
            return entry

    def ipfsTask(self, fn, *args, **kw):
        """ Schedule an async IPFS task """
        return self.loop.create_task(fn(self.ipfsClient,
                                        *args, **kw))

    def ipfsTaskOp(self, fn, *args, **kw):
        """ Schedule an async IPFS task using an IPFSOperator instance """
        client = self.ipfsClient
        if client:
            return self.loop.create_task(fn(
                self.getIpfsOperator(), *args, **kw))

    def getIpfsOperator(self):
        """ Returns a new IPFSOperator with the currently active IPFS client"""
        return IPFSOperator(self.ipfsClient, ctx=self.ipfsCtx,
                            debug=self.debugEnabled,
                            nsCachePath=self.nsCacheLocation)

    def getIpfsConnectionParams(self):
        mgr = self.settingsMgr

        section = CFG_SECTION_IPFSD
        if mgr.isTrue(section, CFG_KEY_ENABLED):
            return IPFSConnectionParams(
                '127.0.0.1',
                mgr.getSetting(section, CFG_KEY_APIPORT),
                mgr.getSetting(section, CFG_KEY_HTTPGWPORT)
            )
        else:
            section = CFG_SECTION_IPFSCONN1
            return IPFSConnectionParams(
                mgr.getSetting(section, CFG_KEY_HOST),
                mgr.getSetting(section, CFG_KEY_APIPORT),
                mgr.getSetting(section, CFG_KEY_HTTPGWPORT)
            )

    def getEthParams(self):
        mgr = self.settingsMgr
        provType = mgr.getSetting(CFG_SECTION_ETHEREUM, CFG_KEY_PROVIDERTYPE)
        rpcUrl = mgr.getSetting(CFG_SECTION_ETHEREUM, CFG_KEY_RPCURL)
        return EthereumConnectionParams(rpcUrl, provType=provType)

    async def updateIpfsClient(self):
        from galacteek.ipfs import ConnectionError

        connParams = self.getIpfsConnectionParams()
        client = aioipfs.AsyncIPFS(host=connParams.host,
                                   port=connParams.apiPort, loop=self.loop)
        self.ipfsClient = client
        self.ipfsCtx.ipfsClient = client
        self.ipfsOpMain = self.getIpfsOperator()
        self.ipfsOpMain.ipidManager = self.ipidManager

        IPFSOpRegistry.regDefault(self.ipfsOpMain)

        try:
            await self.setupRepository()
        except ConnectionError:
            await messageBoxAsync(
                'IPFS connection error (is your daemon running ?)')

        await self.ipfsCtx.ipfsConnectionReady.emit()

    async def stopIpfsServices(self):
        try:
            await self.ipfsCtx.shutdown()
        except BaseException as err:
            log.debug('Error shutting down context: {err}'.format(
                err=str(err)))

        if self.feedFollowerTask is not None:
            await self.feedFollowerTask.close()

    def setupDb(self):
        ensure(self.setupOrmDb(self._mainDbLocation))

    def jobsExceptionHandler(self, scheduler, context):
        pass

    async def setupOrmDb(self, dbpath):
        self.scheduler = await aiojobs.create_scheduler(
            close_timeout=1.0,
            limit=150,
            pending_limit=1000
        )

        # Old database, just for Atom feeds right now

        self.sqliteDb = SqliteDatabase(self._sqliteDbLocation)
        ensure(self.sqliteDb.setup())
        self.modelAtomFeeds = AtomFeedsModel(self.sqliteDb.feeds, parent=self)

        self.urlHistory = history.URLHistory(
            self.sqliteDb,
            enabled=self.settingsMgr.urlHistoryEnabled,
            parent=self
        )

        if not await database.initOrm(self._mainDbLocation):
            await self.dbConfigured.emit(False)
            return

        await self.setupHashmarks()
        await self.dbConfigured.emit(True)

    async def onDbConfigured(self, configured):
        if not configured:
            return

        self.debug('Database ready')

        self.setupClipboard()
        self.setupTranslator()
        self.initSystemTray()
        self.initMisc()
        self.initEthereum()
        self.initWebProfiles()
        self.initDapps()
        self.createMainWindow()
        self.clipboardInit()

        await self.setupIpfsConnection()

    async def fetchGoIpfs(self):
        ipfsPath = self.which('ipfs')
        fsMigratePath = self.which('fs-repo-migrations')

        if fsMigratePath is None or self.cmdArgs.forcegoipfsdl:
            await fetchFsMigrateWrapper(self)

        if ipfsPath is None or self.cmdArgs.forcegoipfsdl:
            path = await fetchGoIpfsWrapper(self)
            if path is None:
                self.systemTrayMessage('Galacteek',
                                       iGoIpfsFetchError())

    async def setupIpfsConnection(self):
        sManager = self.settingsMgr

        await self.fetchGoIpfs()

        if sManager.isTrue(CFG_SECTION_IPFSD, CFG_KEY_ENABLED):
            fsMigratePath = shutil.which('fs-repo-migrations')
            hasFsMigrate = fsMigratePath is not None

            if hasFsMigrate is False and self.cmdArgs.migrate is True:
                self.systemTrayMessage('Galacteek', iFsRepoMigrateNotFound())

            enableMigrate = hasFsMigrate is True and \
                self.cmdArgs.migrate is True

            ipfsPath = self.which('ipfs')

            await self.startIpfsDaemon(
                goIpfsPath=ipfsPath,
                migrateRepo=enableMigrate
            )
        else:
            await self.updateIpfsClient()

    def setupMainObjects(self):
        self.manuals = ManualsManager(self)
        self.mimeDb = QMimeDatabase()
        self.jinjaEnv = defaultJinjaEnv()
        self.solarSystem = SolarSystem()
        self.mimeTypeIcons = preloadMimeIcons()
        self.hmSynchronizer = HashmarksSynchronizer()
        self.ipidManager = IPIDManager(
            resolveTimeout=self.settingsMgr.ipidIpnsTimeout
        )

        self.towers = {
            'dags': DAGSignalsTower(self),
            'schemes': URLSchemesTower(self),
            'did': DIDTower()
        }

        self.rscAnalyzer = ResourceAnalyzer(parent=self)

        self.messageDisplayRequest.connect(
            lambda msg, title: ensure(messageBoxAsync(msg, title=title)))
        self.appImageImported.connect(
            lambda cid: ensure(messageBoxAsync(
                'AppImage was imported in IPFS!')))

    def setupAsyncLoop(self):
        """
        Install the quamash event loop and enable debugging
        """

        loop = QEventLoop(self)
        asyncio.set_event_loop(loop)
        logging.getLogger('quamash').setLevel(logging.INFO)

        if self.debugEnabled:
            logging.getLogger('asyncio').setLevel(logging.DEBUG)
            loop.set_debug(True)
            warnings.simplefilter('always', ResourceWarning)
            warnings.simplefilter('always', BytesWarning)
            warnings.simplefilter('always', ImportWarning)

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

        self.loop = loop
        return loop

    def task(self, fn, *args, **kw):
        return self.loop.create_task(fn(*args, **kw))

    def configure(self):
        self.initSettings()
        self.setupMainObjects()
        self.setupSchemeHandlers()
        self.applyStyle()
        self.setupDb()

    def acquireLock(self):
        lpath = Path(self._pLockLocation)

        if not lpath.exists():
            lpath.touch()

        self.lock = FileLock(lpath, timeout=2)

        try:
            self.lock.acquire()
        except Exception:
            return questionBox(
                'Lock',
                'The profile lock could not be acquired '
                '(another instance could be running). Ignore ?'
            )

        return True

    def setupPaths(self):
        qtDataLocation = QStandardPaths.writableLocation(
            QStandardPaths.DataLocation)

        if not qtDataLocation:
            raise Exception('No writable data location found')

        self._dataLocation = os.path.join(
            qtDataLocation, self._appProfile)

        self._ipfsBinLocation = os.path.join(qtDataLocation, 'ipfs-bin')
        self._ipfsDataLocation = os.path.join(self.dataLocation, 'ipfs')
        self._orbitDataLocation = os.path.join(self.dataLocation, 'orbitdb')
        self._mHashDbLocation = os.path.join(self.dataLocation, 'mhashmetadb')
        self._sqliteDbLocation = os.path.join(self.dataLocation, 'db.sqlite')
        self._pLockLocation = os.path.join(self.dataLocation, 'profile.lock')
        self._mainDbLocation = os.path.join(
            self.dataLocation, 'db_main.sqlite3')
        self.marksDataLocation = os.path.join(self.dataLocation, 'marks')
        self.uiDataLocation = os.path.join(self.dataLocation, 'ui')
        self.cryptoDataLocation = os.path.join(self.dataLocation, 'crypto')
        self.gpgDataLocation = os.path.join(self.cryptoDataLocation, 'gpg')
        self.localMarksFileLocation = os.path.join(self.marksDataLocation,
                                                   'ipfsmarks.local.json')
        self.networkMarksFileLocation = os.path.join(self.marksDataLocation,
                                                     'ipfsmarks.network.json')
        self.pinStatusLocation = os.path.join(self.dataLocation,
                                              'pinstatus.json')
        self._nsCacheLocation = os.path.join(self.dataLocation,
                                             'nscache.json')

        qtConfigLocation = QStandardPaths.writableLocation(
            QStandardPaths.ConfigLocation)
        self.configDirLocation = os.path.join(
            qtConfigLocation, GALACTEEK_NAME, self._appProfile)
        self.settingsFileLocation = os.path.join(
            self.configDirLocation, '{}.conf'.format(GALACTEEK_NAME))

        for dir in [self._ipfsDataLocation,
                    self._mHashDbLocation,
                    self.ipfsBinLocation,
                    self.marksDataLocation,
                    self.cryptoDataLocation,
                    self.gpgDataLocation,
                    self.uiDataLocation,
                    self.configDirLocation]:
            if not os.path.exists(dir):
                os.makedirs(dir)

        self.defaultDownloadsLocation = QStandardPaths.writableLocation(
            QStandardPaths.DownloadLocation)

        self.debug('Datapath: {0}, config: {1}, configfile: {2}'.format(
            self._dataLocation,
            self.configDirLocation,
            self.settingsFileLocation))

        os.environ['PATH'] += os.pathsep + self.ipfsBinLocation

    def which(self, prog='ipfs'):
        path = self.ipfsBinLocation + os.pathsep + os.environ['PATH']
        result = shutil.which(prog, path=path)
        self.debug('Program {0} found at {1}'.format(prog, result))
        return result

    def initSettings(self):
        self.settingsMgr = SettingsManager(path=self.settingsFileLocation)
        setDefaultSettings(self)
        self.settingsMgr.sync()

    async def startIpfsDaemon(self, goIpfsPath='ipfs', migrateRepo=False):
        if self.ipfsd is not None:  # we only support one daemon for now
            return

        pubsubEnabled = True  # mandatory now ..
        corsEnabled = self.settingsMgr.isTrue(CFG_SECTION_IPFSD,
                                              CFG_KEY_CORS)

        sManager = self.settingsMgr
        section = CFG_SECTION_IPFSD

        # Instantiate an IPFS daemon using asyncipfsd and
        # start it in a task, monitoring the initialization process

        self._ipfsd = asyncipfsd.AsyncIPFSDaemon(
            self.ipfsDataLocation, goIpfsPath=goIpfsPath,
            apiport=sManager.getInt(section, CFG_KEY_APIPORT),
            swarmport=sManager.getInt(section, CFG_KEY_SWARMPORT),
            swarmportQuic=sManager.getInt(section, CFG_KEY_SWARMPORT_QUIC),
            swarmProtos=sManager.swarmProtosList,
            gatewayport=sManager.getInt(section, CFG_KEY_HTTPGWPORT),
            swarmLowWater=sManager.getInt(section, CFG_KEY_SWARMLOWWATER),
            swarmHighWater=sManager.getInt(section, CFG_KEY_SWARMHIGHWATER),
            storageMax=sManager.getInt(section, CFG_KEY_STORAGEMAX),
            gwWritable=sManager.isTrue(section, CFG_KEY_HTTPGWWRITABLE),
            routingMode=sManager.getSetting(section, CFG_KEY_ROUTINGMODE),
            pubsubRouter=sManager.getSetting(section, CFG_KEY_PUBSUB_ROUTER),
            namesysPubsub=sManager.isTrue(section, CFG_KEY_NAMESYS_PUBSUB),
            pubsubSigning=sManager.isTrue(section, CFG_KEY_PUBSUB_USESIGNING),
            fileStore=sManager.isTrue(section, CFG_KEY_FILESTORE),
            nice=sManager.getInt(section, CFG_KEY_NICE),
            pubsubEnable=pubsubEnabled, corsEnable=corsEnabled,
            migrateRepo=migrateRepo,
            debug=self.cmdArgs.goipfsdebug,
            offline=self.cmdArgs.offline,
            loop=self.loop)

        await self.scheduler.spawn(self.startIpfsdTask(self.ipfsd))

    async def startIpfsdTask(self, ipfsd):
        started = await ipfsd.start()

        if started is False:
            return self.systemTrayMessage('IPFS', iIpfsDaemonProblem())

        running = False

        logUser.info(iIpfsDaemonStarted())

        # Use asyncio.wait_for to wait for the proto.eventStarted
        # event to be fired.

        for attempt in range(1, 32):
            logUser.info(iIpfsDaemonWaiting(attempt))

            with async_timeout.timeout(1):
                try:
                    await ipfsd.proto.eventStarted.wait()
                except asyncio.CancelledError:
                    continue
                except asyncio.TimeoutError:
                    # Event not set yet, wait again
                    log.debug('IPFSD: timeout occured while waiting for '
                              'daemon to start (attempt: {0})'.format(attempt))
                    continue
                else:
                    # Event was set, good to go
                    logUser.info(iIpfsDaemonReady())
                    running = True
                    break

        if running is True:
            ensure(self.updateIpfsClient())
        else:
            logUser.info(iIpfsDaemonInitProblem())

    def initEthereum(self):
        try:
            from galacteek.dweb.ethereum.ctrl import EthereumController

            self.ethereum = EthereumController(self.getEthParams(),
                                               loop=self.loop, parent=self,
                                               executor=self.executor)
            if self.settingsMgr.ethereumEnabled:
                ensure(self.ethereum.start())
        except ImportError:
            self.ethereum = MockEthereumController()

    def normalCursor(self):
        cursor = QCursor(Qt.ArrowCursor)
        QApplication.setOverrideCursor(cursor)
        QApplication.changeOverrideCursor(cursor)

    def setupClipboard(self):
        self.appClipboard = self.clipboard()
        self.clipTracker = ClipboardTracker(self, self.appClipboard)

    def clipboardInit(self):
        self.clipTracker.clipboardInit()

    def setClipboardText(self, text):
        self.clipTracker.setText(text)

    def getClipboardText(self):
        return self.clipTracker.getText()

    def initWebProfiles(self):
        self.scriptsIpfs = ipfsClientScripts(self.getIpfsConnectionParams())

        self.webProfiles = {
            'minimal': MinimalProfile(parent=self),
            'ipfs': IPFSProfile(parent=self),
            'web3': Web3Profile(parent=self)
        }

    def availableWebProfilesNames(self):
        return [p.profileName for n, p in self.webProfiles.items()]

    def initDapps(self):
        self.dappsRegistry = DappsRegistry(self.ethereum, parent=self)

    def setupSchemeHandlers(self):
        self.dwebSchemeHandler = DWebSchemeHandlerGateway(self)
        self.ensSchemeHandler = EthDNSSchemeHandler(self)
        self.ensProxySchemeHandler = EthDNSProxySchemeHandler(self)
        self.nativeIpfsSchemeHandler = NativeIPFSSchemeHandler(
            self, noMutexes=self.cmdArgs.noipfsmutexlock
        )
        self.qSchemeHandler = MultiObjectHostSchemeHandler(self)

    def subUrl(self, path):
        """ Joins the gatewayUrl and path to form a new URL """
        sub = QUrl(str(self.gatewayUrl))
        sub.setPath(path)
        return sub

    def getJinjaTemplate(self, name):
        try:
            tmpl = self.jinjaEnv.get_template(name)
        except jinja2.exceptions.TemplateNotFound:
            return None
        else:
            return tmpl

    @asyncify
    async def checkReleases(self):
        self.debug('Checking for new releases')
        newR = await pypicheck.newReleaseAvailable()
        if newR:
            self.systemTrayMessage('Galacteek',
                                   iNewReleaseAvailable(), timeout=8000)

    def showTasks(self):
        for task in self.pendingTasks:
            self.debug('Pending task: {}'.format(task))

    def onShowWindow(self):
        self.mainWindow.showMaximized()

    def restart(self):
        ensure(self.restartApp())

    async def restartApp(self):
        from galacteek.guientrypoint import appStarter
        pArgs = self.arguments()

        await self.exitApp()
        time.sleep(1)
        appStarter.startProcess(pArgs)

    def onExit(self):
        ensure(self.exitApp())

    async def shutdownScheduler(self):
        # It ain't that bad. STFS with dignity

        for stry in range(0, 12):
            try:
                async with async_timeout.timeout(2):
                    await self.scheduler.close()
            except asyncio.TimeoutError:
                log.warning(
                    'Timeout shutting down the scheduler (not fooled)')
                continue
            else:
                log.debug(f'Scheduler went down (try: {stry})')
                return

    async def exitApp(self):
        self._shuttingDown = True

        self.lock.release()

        self.mainWindow.stopTimers()

        try:
            self.systemTray.hide()
        except:
            pass

        try:
            await self.sqliteDb.close()
            await database.closeOrm()
        except:
            pass

        await self.stopIpfsServices()

        await self.loop.shutdown_asyncgens()
        await self.shutdownScheduler()
        await cancelAllTasks()

        await self.ethereum.stop()

        if self.ipfsClient:
            await self.ipfsClient.close()

        if self.ipfsd:
            self.ipfsd.stop()

        self.mainWindow.close()

        if self.debug:
            self.showTasks()

        self.tempDir.remove()
        self.quit()


class ManualsManager(QObject):
    """
    Object responsible for importing the HTML manuals in IPFS.

    Also serves as an interface to open specific pages of the manual
    from the application's code
    """

    def __init__(self, app):
        super(ManualsManager, self).__init__()

        self.app = app
        self.registry = {}
        self._schemeHandlers = []
        self.defaultManualLang = 'en'

    def getManualEntry(self, lang):
        return self.registry.get(lang, None)

    @ipfsOp
    async def importManuals(self, ipfsop, profile):
        from galacteek.docs.manual import __manual_en_version__

        documentsList = await ipfsop.filesList(profile.pathDocuments)

        try:
            listing = pkg_resources.resource_listdir(
                'galacteek.docs.manual', '')
            for dir in listing:
                await ipfsop.sleep()

                if dir.startswith('__'):
                    continue

                manualAlreadyImported = False
                lang = dir

                if lang == 'en':
                    manualLinkName = '{name}.manual.{lang}.{ver}'.format(
                        name=GALACTEEK_NAME, lang=lang,
                        ver=__manual_en_version__)
                else:
                    # Just english manual for now
                    continue

                for entry in documentsList:
                    if entry['Name'] == manualLinkName:
                        self.registry[lang] = entry
                        self.app.manualAvailable.emit(lang, entry)
                        manualAlreadyImported = True
                        self.installManualSchemeHandler(entry)
                        break

                if manualAlreadyImported:
                    continue

                entry = await self.importManualLang(lang)
                if entry:
                    await ipfsop.filesLink(entry, profile.pathDocuments,
                                           name=manualLinkName)
        except Exception as e:
            log.debug('Failed importing manuals: {0}'.format(str(e)))

    async def importManualLang(self, lang):
        try:
            docPath = pkg_resources.resource_filename('galacteek.docs.manual',
                                                      '{0}/html'.format(lang))
            entry = await self.importDocPath(docPath, lang)
        except Exception as e:
            log.debug('Failed importing manual ({0}) {1}'.format(
                lang, str(e)))
        else:
            return entry

    @ipfsOp
    async def importDocPath(self, ipfsop, docPath, lang):
        docEntry = await ipfsop.addPath(docPath)
        if docEntry:
            await ipfsop.sleep()
            self.registry[lang] = docEntry

            self.app.manualAvailable.emit(lang, docEntry)
            self.installManualSchemeHandler(docEntry)
            return docEntry

    def installManualSchemeHandler(self, docEntry):
        """
        Install an object proxy scheme handler to be able
        to just type 'manual:/' to access the manual from
        the browser
        """

        handler = ObjectProxySchemeHandler(
            self.app, IPFSPath(docEntry['Hash']))

        for pName, profile in self.app.webProfiles.items():
            profile.installHandler(SCHEME_MANUAL, handler)

        # Need to keep a reference somewhere for Qt
        self._schemeHandlers.append(handler)

    def browseManualPage(self, pagePath, fragment=None):
        manual = self.registry.get(self.defaultManualLang)
        if not manual:
            return False

        manualPath = IPFSPath(manual['Hash'])
        if not manualPath.valid:
            return False

        ipfsPath = manualPath.child(pagePath)
        ipfsPath.fragment = fragment

        ensure(self.app.resourceOpener.open(ipfsPath))
