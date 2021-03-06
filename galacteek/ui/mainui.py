import functools

from logbook import Handler
from logbook import StringFormatterHandlerMixin

from PyQt5.QtWidgets import QTabWidget
from PyQt5.QtWidgets import QMainWindow
from PyQt5.QtWidgets import QWidget
from PyQt5.QtWidgets import QToolBar
from PyQt5.QtWidgets import QMenu
from PyQt5.QtWidgets import QAction
from PyQt5.QtWidgets import QActionGroup
from PyQt5.QtWidgets import QToolButton
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtWidgets import QLabel
from PyQt5.QtWidgets import QVBoxLayout
from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtWidgets import QLineEdit
from PyQt5.QtWidgets import QStackedWidget

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtCore import Qt
from PyQt5.QtCore import QUrl
from PyQt5.QtCore import QTimer
from PyQt5.QtCore import QDateTime
from PyQt5.QtCore import QSize
from PyQt5.QtCore import QPoint

from PyQt5.Qt import QSizePolicy

from PyQt5.QtGui import QKeySequence
from PyQt5.QtGui import QTextCursor
from PyQt5.QtGui import QTextDocument

from PyQt5 import QtWebEngineWidgets

from galacteek import GALACTEEK_NAME
from galacteek import ensure
from galacteek import partialEnsure
from galacteek import log
from galacteek import database
from galacteek.core.glogger import loggerMain
from galacteek.core.glogger import loggerUser
from galacteek.core.glogger import easyFormatString
from galacteek.core.asynclib import asyncify
from galacteek.ui import mediaplayer
from galacteek.ipfs.wrappers import *
from galacteek.ipfs.ipfsops import *
from galacteek.ipfs.cidhelpers import IPFSPath

from galacteek.dweb.page import HashmarksPage, DWebView, WebTab
from galacteek.core.modelhelpers import *

from . import ui_ipfsinfos

from . import userwebsite
from . import browser
from . import files
from . import keys
from . import settings
from . import orbital
from . import textedit
from . import ipfsview
from . import ipfssearch
from . import peers
from . import eventlog
from . import pin
from . import chat

from .dids import DIDExplorer
from .clips import RotatingCubeClipSimple
from .clips import RotatingCubeRedFlash140d
from .eth import EthereumStatusButton
from .feeds import AtomFeedsViewTab
from .feeds import AtomFeedsView
from .textedit import TextEditorTab
from .iprofile import ProfileEditDialog
from .iprofile import ProfileButton
from .peers import PeersServiceSearchDock
from .pubsub import PubsubSnifferWidget
from .pyramids import MultihashPyramidsToolBar
from .quickaccess import QuickAccessToolBar
from .daemonstats import BandwidthGraphView
from .daemonstats import PeersCountGraphView
from .camera import CameraController
from .helpers import *
from .widgets import PopupToolButton
from .widgets import HashmarkMgrButton
from .widgets import HashmarksSearcher
from .widgets import AtomFeedsToolbarButton
from .widgets import AnimatedLabel
from .dialogs import *
from ..appsettings import *
from .i18n import *

from .clipboard import ClipboardManager
from .clipboard import ClipboardItemsStack


def iPinningItemStatus(pinPath, pinProgress):
    return QCoreApplication.translate(
        'GalacteekWindow',
        '\nPath: {0}, nodes processed: {1}').format(pinPath, pinProgress)


def iAbout():
    from galacteek import __version__
    return QCoreApplication.translate('GalacteekWindow', '''
        <p align='center'>
        <img src=':/share/icons/galacteek.png' />
        </p>

        <p>
        <b>galacteek</b> is a multi-platform Qt5-based browser
        for the distributed web
        </p>
        <br/>
        <p>Contact:
            <a href="mailto: galacteek@protonmail.com">
                galacteek@protonmail.com
            </a>
        </p>
        <p>Authors: see
        <a href="https://github.com/pinnaculum/galacteek/blob/master/AUTHORS.rst">
            AUTHORS.rst
        </a>
        </p>
        <p>galacteek version {0}</p>''').format(__version__)  # noqa


class UserLogsWindow(QMainWindow):
    hidden = pyqtSignal()

    def __init__(self):
        super(UserLogsWindow, self).__init__()
        self.toolbar = QToolBar(self)
        self.toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

        self.logsBrowser = QTextEdit(self)
        self.logsBrowser.setReadOnly(True)
        self.logsBrowser.setObjectName('logsTextWidget')
        self.logsBrowser.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.searchBack = QPushButton('Search backward')
        self.searchBack.clicked.connect(self.onSearchBack)
        self.searchFor = QPushButton('Search forward')
        self.searchFor.clicked.connect(self.onSearchForward)

        self.logsSearcher = QLineEdit(self)
        self.logsSearcher.setClearButtonEnabled(True)
        self.toolbar.addWidget(self.logsSearcher)
        self.toolbar.addWidget(self.searchBack)
        self.toolbar.addWidget(self.searchFor)
        self.logsSearcher.returnPressed.connect(self.onSearchBack)
        self.logsSearcher.textChanged.connect(self.onSearchTextChanged)
        self.setCentralWidget(self.logsBrowser)

    def onSearchTextChanged(self):
        pass

    def onSearchBack(self):
        flags = QTextDocument.FindCaseSensitively | QTextDocument.FindBackward
        self.searchText(flags)

    def onSearchForward(self):
        flags = QTextDocument.FindCaseSensitively
        self.searchText(flags)

    def searchText(self, flags):
        text = self.logsSearcher.text()
        if text:
            self.logsBrowser.find(text, flags)

    def hideEvent(self, event):
        self.hidden.emit()
        super().hideEvent(event)


class MainWindowLogHandler(Handler, StringFormatterHandlerMixin):
    """
    Custom logbook handler that logs to the status bar

    Should be moved to a separate module
    """

    modulesColorTable = {
        'galacteek.ui.resource': '#7f8491',
        'galacteek.did.ipid': '#FA8A47',
        'galacteek.core.profile': 'blue'
    }

    def __init__(self, logsBrowser, application_name=None, address=None,
                 facility='user', level=0, format_string=None,
                 filter=None, bubble=True, window=None):
        Handler.__init__(self, level, filter, bubble)
        StringFormatterHandlerMixin.__init__(self, easyFormatString)
        self.application_name = application_name
        self.window = window
        self.logsBrowser = logsBrowser

    def emit(self, record):
        fRecord = self.format(record)

        if record.level_name == 'INFO':
            color = self.modulesColorTable.get(record.module, 'black')
            self.window.statusMessage(
                "<p style='color: {color}'>{msg}</p>\n".format(
                    color=color, msg=fRecord))

        self.logsBrowser.append(fRecord)

        if not self.logsBrowser.isVisible():
            self.logsBrowser.moveCursor(QTextCursor.End)


class IPFSDaemonStatusWidget(QWidget):
    def __init__(self, bwStatsView, peersStatsView, parent=None):
        super(IPFSDaemonStatusWidget, self).__init__(
            parent, Qt.Popup | Qt.FramelessWindowHint)

        self.app = QApplication.instance()
        self.ui = ui_ipfsinfos.Ui_IPFSInfosDialog()
        self.ui.setupUi(self)
        self.setWindowTitle(iIpfsInfos())

        self.labels = [self.ui.repoObjCount,
                       self.ui.repoVersion,
                       self.ui.repoSize,
                       self.ui.repoMaxStorage,
                       self.ui.nodeId,
                       self.ui.agentVersion,
                       self.ui.protocolVersion]

        self.ui.bwRateStatsLayout.addWidget(bwStatsView)
        self.ui.peersStatsLayout.addWidget(peersStatsView)

        self.setMinimumWidth(self.app.desktopGeometry.width() / 2)
        self.ui.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.ui.tabWidget.setStyleSheet(
            "QTabWidget::pane { background-color: lightgray; }")

    def enableLabels(self, enable):
        for label in self.labels:
            if not enable:
                label.setText('Fetching ...')

            label.setEnabled(enable)

    @ipfsOp
    async def update(self, ipfsop):
        self.enableLabels(False)
        try:
            repoStat = await ipfsop.client.repo.stat()
            idInfo = await ipfsop.client.core.id()
        except BaseException:
            for label in self.labels:
                label.setText(iUnknown())
        else:
            self.ui.repoObjCount.setText(str(repoStat.get(
                'NumObjects', iUnknown())))
            self.ui.repoVersion.setText(str(repoStat.get(
                'Version', iUnknown())))
            self.ui.repoSize.setText(sizeFormat(repoStat.get(
                'RepoSize', 0)))
            self.ui.repoMaxStorage.setText(sizeFormat(repoStat.get(
                'StorageMax', 0)))

            self.ui.nodeId.setText(idInfo.get('ID', iUnknown()))
            self.ui.agentVersion.setText(idInfo.get(
                'AgentVersion', iUnknown()))
            self.ui.protocolVersion.setText(idInfo.get('ProtocolVersion',
                                                       iUnknown()))
            self.enableLabels(True)


class DatabasesManager(QObject):
    def __init__(self, orbitConnector, parent=None):
        super().__init__(parent)

        self.mainW = parent

        self.icon = getIcon('orbitdb.png')
        self.connector = orbitConnector
        self._dbButton = self.buildButton()

    @property
    def button(self):
        return self._dbButton

    def buildButton(self):
        dbButton = QToolButton(self)
        dbButton.setIconSize(QSize(24, 24))
        dbButton.setIcon(self.icon)
        dbButton.setPopupMode(QToolButton.MenuButtonPopup)

        self.databasesMenu = QMenu(self)
        self.mainFeedAction = QAction(self.icon,
                                      'General discussions feed', self,
                                      triggered=self.onMainFeed)
        self.databasesMenu.addAction(self.mainFeedAction)
        dbButton.setMenu(self.databasesMenu)
        return dbButton

    def onMainFeed(self):
        database = self.connector.database('feeds', 'general')
        view = orbital.OrbitFeedView(self.connector, database,
                                     parent=self.mainW.tabWidget)
        self.mainW.registerTab(view, 'General', current=True,
                               icon=self.icon)


class MiscToolBar(QToolBar):
    moved = pyqtSignal(int)

    def __init__(self, parent):
        super().__init__(parent)

        self.setObjectName('toolbarMisc')
        self.setAllowedAreas(
            Qt.RightToolBarArea
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def contextMenuEvent(self, event):
        # no context menu
        pass


class MainToolBar(QToolBar):
    moved = pyqtSignal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('toolbarMain')

        self.setFloatable(False)
        self.setAllowedAreas(
            Qt.LeftToolBarArea | Qt.TopToolBarArea
        )
        self.setContextMenuPolicy(Qt.NoContextMenu)

        # Empty widget
        self.emptySpace = QWidget()
        self.emptySpace.setSizePolicy(
            QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.lastPos = None

    @property
    def vertical(self):
        return self.orientation() == Qt.Vertical

    @property
    def horizontal(self):
        return self.orientation() == Qt.Horizontal

    def dragEnterEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        pass


class TabWidgetKeyFilter(QObject):
    nextPressed = pyqtSignal()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            modifiers = event.modifiers()

            key = event.key()
            if modifiers & Qt.ControlModifier:
                if key == Qt.Key_J:
                    self.nextPressed.emit()
                    return True
        return False


class CentralWidget(QWidget):
    def __init__(self, parent):
        super(CentralWidget, self).__init__(parent)

        self.setObjectName('centralWidget')
        self.wLayout = QVBoxLayout()
        self.setLayout(self.wLayout)


class BackgroundWidget(QWidget):
    def __init__(self, parent):
        super(BackgroundWidget, self).__init__(parent)

        self.setObjectName('homeWidget')
        self.wLayout = QVBoxLayout()
        self.setLayout(self.wLayout)


class MainTabWidget(QTabWidget):
    onTabInserted = pyqtSignal(int)
    onTabRemoved = pyqtSignal(int)

    def tabInserted(self, index):
        self.onTabInserted.emit(index)
        super().tabInserted(index)

    def tabRemoved(self, index):
        self.onTabRemoved.emit(index)
        super().tabRemoved(index)


class BrowseButton(PopupToolButton):
    """
    Browse button. When the button is hovered we play the
    rotating cube clip.
    """

    def __init__(self, parent):
        super().__init__(parent=parent, mode=QToolButton.InstantPopup)

        self.animatedActions = []
        self.rotatingCubeClip = RotatingCubeClipSimple()
        self.rotatingCubeClip.finished.connect(
            functools.partial(self.rotatingCubeClip.start))
        self.rotatingCubeClip.frameChanged.connect(self.onCubeClipFrame)
        self.normalIcon()

    def setMenu(self, menu):
        super(BrowseButton, self).setMenu(menu)

        menu.triggered.connect(lambda action: self.normalIcon())
        menu.aboutToHide.connect(self.normalIcon)

    def onCubeClipFrame(self, no):
        icon = self.rotatingCubeClip.createIcon()

        self.setIcon(icon)

        if self.menu and self.menu.isVisible():
            for action in self.menu.actions():
                if action in self.animatedActions:
                    action.setIcon(icon)

    def rotateCube(self):
        if not self.rotatingCubeClip.playing():
            self.rotatingCubeClip.start()

    def normalIcon(self):
        self.rotatingCubeClip.stop()
        self.setIcon(getIconIpfs64())

    def enterEvent(self, event):
        self.rotateCube()
        super(BrowseButton, self).enterEvent(event)

    def leaveEvent(self, event):
        if not self.menu.isVisible() and self.isEnabled():
            self.normalIcon()

        super(BrowseButton, self).leaveEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, app):
        super(MainWindow, self).__init__()

        self.setWindowFlags(
            Qt.Window |
            Qt.CustomizeWindowHint |
            Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint
        )

        self.showMaximized()
        self._app = app
        self._allTabs = []
        self._lastFeedMark = None

        self.centralWidget = CentralWidget(self)
        self.menuBar().hide()

        # Seems reasonable
        self.setMinimumSize(QSize(
            self.app.desktopGeometry.width() / 2,
            self.app.desktopGeometry.height() / 2)
        )

        # User logs widget
        self.logsPopupWindow = UserLogsWindow()
        self.logsPopupWindow.setObjectName('logsTextWindow')
        self.logsPopupWindow.setWindowTitle('{}: logs'.format(GALACTEEK_NAME))
        self.logsPopupWindow.hide()

        self.logsPopupWindow.setMinimumSize(QSize(
            (2 * self.app.desktopGeometry.width()) / 3,
            self.app.desktopGeometry.height() / 2
        ))

        self.userLogsHandler = MainWindowLogHandler(
            self.logsPopupWindow.logsBrowser, window=self,
            level='DEBUG' if self.app.debugEnabled else 'INFO')

        loggerMain.handlers.append(self.userLogsHandler)
        loggerUser.handlers.append(self.userLogsHandler)

        self.tabnFManager = iFileManager()
        self.tabnKeys = iKeys()
        self.tabnPinning = iPinningStatus()
        self.tabnMediaPlayer = iMediaPlayer()
        self.tabnHashmarks = iHashmarks()
        self.tabnChat = iChat()
        self.tabnFeeds = iAtomFeeds()

        self.menuManual = QMenu(iManual(), self)

        # DID explorer
        self.didExplorer = DIDExplorer()

        # Global pin-all button
        self.pinAllGlobalButton = QToolButton(self)
        self.pinAllGlobalButton.setIcon(getIcon('pin.png'))
        self.pinAllGlobalButton.setObjectName('pinGlobalButton')
        self.pinAllGlobalButton.setToolTip(iGlobalAutoPinning())
        self.pinAllGlobalButton.setCheckable(True)
        self.pinAllGlobalButton.setAutoRaise(True)
        self.pinAllGlobalChecked = False
        self.pinAllGlobalButton.toggled.connect(self.onToggledPinAllGlobal)
        self.pinAllGlobalButton.setChecked(self.app.settingsMgr.browserAutoPin)

        self.toolbarMain = MainToolBar(self)
        self.toolbarPyramids = MultihashPyramidsToolBar(self)

        self.toolbarMain.orientationChanged.connect(self.onMainToolbarMoved)
        self.toolbarTools = QToolBar()
        self.toolbarTools.setOrientation(self.toolbarMain.orientation())

        # Apps/shortcuts toolbar
        self.qaToolbar = QuickAccessToolBar(self)
        self.qaToolbar.setOrientation(self.toolbarMain.orientation())

        # Main actions and browse button setup
        self.quitAction = QAction(getIcon('quit.png'),
                                  iQuit(),
                                  shortcut=QKeySequence('Ctrl+q'),
                                  triggered=self.quit)
        self.quitAction.setShortcutVisibleInContextMenu(True)

        self.restartAction = QAction(getIcon('quit.png'),
                                     iRestart(),
                                     shortcut=QKeySequence('Alt+Shift+r'),
                                     triggered=self.app.restart)

        self.mPlayerOpenAction = QAction(getIcon('multimedia.png'),
                                         iMediaPlayer(),
                                         triggered=self.onOpenMediaPlayer)

        self.psniffAction = QAction(getIcon('network-transmit.png'),
                                    iPubSubSniff(),
                                    self,
                                    triggered=self.openPsniffTab)

        self.searchServicesAction = QAction(getIcon('ipservice.png'),
                                            'Search IP services',
                                            self,
                                            shortcut=QKeySequence('Ctrl+i'),
                                            triggered=self.onSearchServices)

        self.editorOpenAction = QAction(getIcon('text-editor.png'),
                                        iTextEditor(),
                                        triggered=self.addEditorTab)

        self.hashmarksManagerAction = QAction(getIcon('hashmarks.png'),
                                              iHashmarksManager(),
                                              shortcut=QKeySequence('Ctrl+m'),
                                              triggered=self.addHashmarksTab)

        self.editorOpenAction = QAction(getIcon('text-editor.png'),
                                        iTextEditor(),
                                        triggered=self.addEditorTab)

        self.browseAction = QAction(
            getIconIpfsIce(),
            iBrowse(),
            shortcut=QKeySequence('Ctrl+t'),
            triggered=self.onOpenBrowserTabClicked)
        self.browseAction.setShortcutVisibleInContextMenu(True)
        self.browseAutopinAction = QAction(
            getIconIpfsIce(),
            iBrowseAutoPin(),
            shortcut=QKeySequence('Ctrl+Alt+t'),
            triggered=functools.partial(self.onOpenBrowserTabClicked,
                                        pinBrowsed=True))
        self.browseAutopinAction.setShortcutVisibleInContextMenu(True)

        self.chatAction = QAction(
            getIcon('chat.png'),
            'Chat',
            triggered=self.onOpenChatWidget
        )

        self.seedAppImageAction = QAction(
            getIcon('appimage.png'),
            'Seed AppImage',
            triggered=self.onSeedAppImage
        )

        self.browseButton = BrowseButton(self)
        self.browseButton.setPopupMode(QToolButton.InstantPopup)
        self.browseButton.setObjectName('buttonBrowseIpfs')
        self.browseButton.normalIcon()

        self.browseButton.menu.addAction(self.browseAction)
        self.browseButton.menu.addAction(self.browseAutopinAction)
        self.browseButton.menu.addSeparator()
        self.browseButton.menu.addAction(self.searchServicesAction)
        self.browseButton.menu.addSeparator()
        self.browseButton.menu.addAction(self.editorOpenAction)
        self.browseButton.menu.addSeparator()
        self.browseButton.menu.addAction(self.mPlayerOpenAction)
        self.browseButton.menu.addSeparator()

        if not self.app.cmdArgs.seed and self.app.cmdArgs.appimage:
            # Add the possibility to import the image from the menu
            # if not specified on the command-line
            self.browseButton.menu.addAction(self.seedAppImageAction)
            self.browseButton.menu.addSeparator()

        self.browseButton.menu.addAction(self.quitAction)

        self.browseButton.animatedActions = [
            self.browseAction,
            self.browseAutopinAction
        ]
        self.browseButton.rotateCube()

        # File manager button
        self.fileManagerButton = QToolButton(self)
        self.fileManagerButton.setToolTip(iFileManager())
        self.fileManagerButton.setIcon(getIcon('folder-open.png'))
        self.fileManagerButton.clicked.connect(self.onFileManagerClicked)
        self.fileManagerButton.setShortcut(QKeySequence('Ctrl+Alt+f'))

        # File manager
        self.fileManagerWidget = files.FileManager(parent=self)

        # Text editor button
        self.textEditorButton = QToolButton(self)
        self.textEditorButton.setToolTip(iTextEditor())
        self.textEditorButton.setIcon(getIcon('text-editor.png'))
        self.textEditorButton.clicked.connect(self.addEditorTab)

        # Atom Feeds
        self.atomButton = AtomFeedsToolbarButton(self)
        self.atomButton.clicked.connect(self.onShowAtomFeeds)
        self.atomFeedsViewWidget = AtomFeedsView(self.app.modelAtomFeeds)

        # Camera controller
        self.cameraController = CameraController(parent=self)

        # Edit-Profile button
        self.menuUserProfile = QMenu(self)
        self.profilesActionGroup = QActionGroup(self)

        # Profile button
        self.profileMenu = QMenu(self)
        iconProfile = getIcon('helmet.png')
        self.profileMenu.addAction(iconProfile,
                                   'Edit profile',
                                   self.onProfileEditDialog)
        self.profileMenu.addSeparator()

        self.userWebsiteManager = userwebsite.UserWebsiteManager(
            parent=self.profileMenu)
        self.profileMenu.addMenu(self.userWebsiteManager.blogMenu)

        self.profileButton = ProfileButton(
            menu=self.profileMenu,
            icon=iconProfile
        )
        self.profileButton.setEnabled(False)

        # Hashmarks mgr button

        # Hashmarks searcher
        self.hashmarksSearcher = HashmarksSearcher(parent=self)
        self.hashmarksSearcher.setToolTip(iHashmarksLibrary())
        self.hashmarksSearcher.setShortcut(QKeySequence('Ctrl+Alt+h'))

        self.hashmarkMgrButton = HashmarkMgrButton(
            marks=self.app.marksLocal)
        self.hashmarkMgrButton.menu.addMenu(self.hashmarksSearcher.menu)

        self.hashmarkMgrButton.menu.addSeparator()

        # Peers button
        self.peersButton = QToolButton(self)
        self.peersButton.setToolTip('Peers')
        self.peersButton.setIcon(getIcon('peers.png'))
        self.peersButton.clicked.connect(self.onPeersMgrClicked)

        self.clipboardItemsStack = ClipboardItemsStack(parent=self.toolbarMain)

        # Clipboard loader button
        self.clipboardManager = ClipboardManager(
            self.app.clipTracker,
            self.clipboardItemsStack,
            self.app.resourceOpener,
            icon=getIcon('clipboard.png'),
            parent=self.toolbarMain
        )

        # Chat center button
        self.chatCenterButton = chat.ChatCenterButton(parent=self.toolbarMain)

        # Settings button
        settingsIcon = getIcon('settings.png')
        self.settingsToolButton = QToolButton(self)
        self.settingsToolButton.setIcon(settingsIcon)
        self.settingsToolButton.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        menu.addAction(settingsIcon, iSettings(),
                       self.onSettings)
        menu.addSeparator()
        menu.addAction(settingsIcon, iEventLog(),
                       self.onOpenEventLog)
        menu.addAction(getIcon('lock-and-key.png'), iKeys(),
                       self.onIpfsKeysClicked)
        menu.addSeparator()
        menu.addAction(self.psniffAction)
        menu.addSeparator()
        menu.addAction(iClearHistory(), self.onClearHistory)

        self.settingsToolButton.setMenu(menu)

        # Help button
        self.helpToolButton = QToolButton(self)
        self.helpToolButton.setObjectName('helpToolButton')
        self.helpToolButton.setIcon(getIcon('information.png'))
        self.helpToolButton.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        menu.addMenu(self.menuManual)
        menu.addSeparator()
        menu.addAction('Donate', self.onHelpDonate)
        menu.addAction('About', self.onAboutGalacteek)
        self.helpToolButton.setMenu(menu)

        # Quit button
        self.quitButton = PopupToolButton(
            parent=self, mode=QToolButton.InstantPopup)
        self.quitButton.setObjectName('quitToolButton')
        self.quitButton.setIcon(self.quitAction.icon())
        self.quitButton.menu.addAction(self.restartAction)
        self.quitButton.menu.addSeparator()
        self.quitButton.menu.addAction(self.quitAction)
        self.quitButton.setToolTip(iQuit())

        self.ipfsSearchPageFactory = ipfssearch.SearchResultsPageFactory(self)

        self.ipfsSearchButton = ipfssearch.IPFSSearchButton(self)
        self.ipfsSearchButton.setShortcut(QKeySequence('Ctrl+Alt+s'))
        self.ipfsSearchButton.setIcon(self.ipfsSearchButton.iconNormal)
        self.ipfsSearchButton.setToolTip(iSearchIpfsContent())
        self.ipfsSearchButton.clicked.connect(self.addIpfsSearchView)

        self.toolbarMain.addWidget(self.browseButton)
        self.toolbarMain.addWidget(self.hashmarkMgrButton)
        self.toolbarMain.addWidget(self.hashmarksSearcher)
        self.toolbarMain.addWidget(self.profileButton)
        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.chatCenterButton)
        self.toolbarMain.addWidget(self.peersButton)
        self.toolbarMain.addWidget(self.atomButton)

        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.fileManagerButton)
        self.toolbarMain.addWidget(self.textEditorButton)
        self.toolbarMain.addWidget(self.cameraController)

        self.hashmarkMgrButton.hashmarkClicked.connect(self.onHashmarkClicked)
        self.hashmarksSearcher.hashmarkClicked.connect(self.onHashmarkClicked)

        self.toolbarMain.addSeparator()

        self.toolbarMain.addWidget(self.toolbarTools)
        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.qaToolbar)

        self.toolbarMain.actionStatuses = self.toolbarMain.addAction(
            'Statuses')
        self.toolbarMain.actionStatuses.setVisible(False)

        self.toolbarMain.addWidget(self.toolbarMain.emptySpace)
        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.ipfsSearchButton)
        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.clipboardItemsStack)
        self.toolbarMain.addWidget(self.clipboardManager)
        self.toolbarMain.addSeparator()

        self.toolbarMain.addWidget(self.pinAllGlobalButton)
        self.toolbarMain.addWidget(self.settingsToolButton)

        self.toolbarMain.addWidget(self.helpToolButton)
        self.toolbarMain.addSeparator()
        self.toolbarMain.addWidget(self.quitButton)

        self.addToolBar(Qt.TopToolBarArea, self.toolbarMain)
        self.addToolBar(Qt.RightToolBarArea, self.toolbarPyramids)

        self.stack = QStackedWidget(self)

        self.bgWidget = BackgroundWidget(self.stack)

        self.tabWidget = MainTabWidget(self.stack)
        self.tabWidget.onTabInserted.connect(self.onNewTab)
        self.tabWidget.onTabRemoved.connect(self.onTabRemoved)
        self.tabWidget.setObjectName('tabWidget')
        self.tabWidget.setTabsClosable(True)
        self.tabWidget.tabCloseRequested.connect(
            partialEnsure(self.onTabCloseRequest))
        self.tabWidget.setElideMode(Qt.ElideMiddle)
        self.tabWidget.setUsesScrollButtons(True)
        self.tabWidget.currentChanged.connect(self.onTabChanged)

        self.stack.addWidget(self.bgWidget)
        self.stack.addWidget(self.tabWidget)

        self.centralWidget.wLayout.addWidget(self.stack)

        if self.app.system != 'Darwin':
            self.tabWidget.setDocumentMode(True)

        tabKeyFilter = TabWidgetKeyFilter(self)
        tabKeyFilter.nextPressed.connect(self.cycleTabs)
        self.tabWidget.installEventFilter(tabKeyFilter)

        self.webProfile = QtWebEngineWidgets.QWebEngineProfile.defaultProfile()

        # Status bar setup
        self.pinningStatusButton = QPushButton(self)
        self.pinningStatusButton.setShortcut(
            QKeySequence('Ctrl+u'))
        self.pinningStatusButton.setToolTip(iNoStatus())
        self.pinningStatusButton.setIcon(getIcon('pin-black.png'))
        self.pinningStatusButton.clicked.connect(
            self.showPinningStatusWidget)
        self.pubsubStatusButton = QPushButton(self)
        self.pubsubStatusButton.setIcon(getIcon('network-offline.png'))

        self.ipfsStatusCube = AnimatedLabel(
            RotatingCubeRedFlash140d(speed=10),
            parent=self
        )
        self.ipfsStatusCube.clip.setScaledSize(QSize(24, 24))
        self.ipfsStatusCube.hovered.connect(self.onIpfsInfosHovered)
        self.ipfsStatusCube.startClip()

        self.statusbar = self.statusBar()
        self.userLogsButton = QToolButton(self)
        self.userLogsButton.setToolTip('Logs')
        self.userLogsButton.setIcon(getIcon('information.png'))
        self.userLogsButton.setCheckable(True)
        self.userLogsButton.toggled.connect(self.onShowUserLogs)
        self.logsPopupWindow.hidden.connect(
            functools.partial(self.userLogsButton.setChecked, False))

        # Bandwidth graph
        self.bwGraphView = BandwidthGraphView(parent=None)
        self.peersGraphView = PeersCountGraphView(parent=None)
        self.ipfsDaemonStatusWidget = IPFSDaemonStatusWidget(
            self.bwGraphView, self.peersGraphView)

        self.lastLogLabel = QLabel(self.statusbar)
        self.lastLogLabel.setAlignment(Qt.AlignLeft)
        self.lastLogLabel.setObjectName('lastLogLabel')
        self.statusbar.insertWidget(0, self.lastLogLabel, 1)

        self.statusbar.addPermanentWidget(self.ipfsStatusCube)

        self.ethereumStatusBtn = EthereumStatusButton(parent=self)
        self.statusbar.addPermanentWidget(self.ethereumStatusBtn)

        self.statusbar.addPermanentWidget(self.pinningStatusButton)
        self.statusbar.addPermanentWidget(self.pubsubStatusButton)
        self.statusbar.addPermanentWidget(self.userLogsButton)

        # Connection status timer
        self.timerStatus = QTimer(self)
        self.timerStatus.timeout.connect(self.onMainTimerStatus)
        self.timerStatus.start(7000)

        self.enableButtons(False)

        # Docks
        self.pSearchDock = PeersServiceSearchDock(self.app.peersTracker, self)
        self.pSearchDock.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.pSearchDock)

        # Connect the IPFS context signals
        self.app.ipfsCtx.ipfsConnectionReady.connectTo(self.onConnReady)
        self.app.ipfsCtx.ipfsRepositoryReady.connectTo(self.onRepoReady)

        self.app.ipfsCtx.pubsub.psMessageRx.connect(self.onPubsubRx)
        self.app.ipfsCtx.pubsub.psMessageTx.connect(self.onPubsubTx)
        self.app.ipfsCtx.profilesAvailable.connect(self.onProfilesList)
        self.app.ipfsCtx.profileChanged.connect(self.onProfileChanged)
        self.app.ipfsCtx.pinItemStatusChanged.connect(self.onPinStatusChanged)
        self.app.ipfsCtx.pinItemsCount.connect(self.onPinItemsCount)
        self.app.ipfsCtx.pinFinished.connect(self.onPinFinished)

        # Misc signals
        database.IPNSFeedMarkAdded.connectTo(self.onFeedMarkAdded)
        self.app.systemTray.messageClicked.connect(self.onSystrayMsgClicked)

        # Application signals
        self.app.manualAvailable.connect(self.onManualAvailable)

        self.tabWidget.removeTab(0)

        previousGeo = self.app.settingsMgr.mainWindowGeometry
        if previousGeo:
            self.restoreGeometry(previousGeo)
        previousState = self.app.settingsMgr.mainWindowState
        if previousState:
            self.restoreState(previousState)

        self.hashmarksPage = None
        self.pinStatusTab = pin.PinStatusWidget(self)

        self.pinIconLoading = getIcon('pin-blue-loading.png')
        self.pinIconNormal = getIcon('pin-black.png')

        self.setCentralWidget(self.centralWidget)

    @property
    def app(self):
        return self._app

    @property
    def allTabs(self):
        return self._allTabs

    @property
    def searchBar(self):
        return self.centralWidget.searchBar

    def onNewTab(self, idx):
        if self.stack.currentIndex() != 1:
            self.stack.setCurrentIndex(1)

    def onTabRemoved(self, idx):
        if self.tabWidget.count() == 0:
            self.stack.setCurrentIndex(0)
        else:
            if self.stack.currentIndex() != 1:
                self.stack.setCurrentIndex(1)

    def cycleTabs(self):
        curIndex = self.tabWidget.currentIndex()
        if curIndex + 1 < self.tabWidget.count():
            self.tabWidget.setCurrentIndex(curIndex + 1)
        else:
            self.tabWidget.setCurrentIndex(0)

    def onSeedAppImage(self):
        ensure(self.app.seedAppImage())

    def onClearHistory(self):
        self.app.urlHistory.clear()

    def onHashmarkClicked(self, hashmark):
        ensure(self.app.resourceOpener.open(
            hashmark.path if hashmark.path else hashmark.url,
            schemePreferred=hashmark.schemepreferred,
            pin=True if hashmark.pin != 0 else False))

    def onMainToolbarMoved(self, orientation):
        self.toolbarMain.lastPos = self.toolbarMain.pos()

        self.toolbarTools.setOrientation(orientation)
        self.qaToolbar.setOrientation(orientation)

        if self.toolbarMain.vertical:
            self.toolbarMain.emptySpace.setSizePolicy(
                QSizePolicy.Minimum, QSizePolicy.Expanding)
            self.qaToolbar.setMinimumHeight(
                self.toolbarMain.height() / 3)
            self.qaToolbar.setMinimumWidth(32)
        elif self.toolbarMain.horizontal:
            self.qaToolbar.setMinimumWidth(
                self.width() / 3)
            self.qaToolbar.setMinimumHeight(32)
            self.toolbarMain.emptySpace.setSizePolicy(QSizePolicy.Expanding,
                                                      QSizePolicy.Minimum)

    def onOpenEventLog(self):
        self.addEventLogTab(current=True)

    def onShowUserLogs(self, checked):
        lowerPos = self.mapToGlobal(QPoint(self.width(), self.height()))

        popupPoint = QPoint(
            lowerPos.x() - self.logsPopupWindow.width() - 64,
            lowerPos.y() - self.logsPopupWindow.height() - 64
        )

        self.logsPopupWindow.move(popupPoint)
        self.logsPopupWindow.logsBrowser.moveCursor(QTextCursor.End)
        self.logsPopupWindow.setVisible(checked)

    def onProfileEditDialog(self):
        runDialog(ProfileEditDialog, self.app.ipfsCtx.currentProfile,
                  title='IP profile')

    def onProfileWebsiteUpdated(self):
        self.profileButton.setStyleSheet('''
            QToolButton {
                background-color: #B7CDC2;
            }
        ''')
        self.app.loop.call_later(
            3, self.profileButton.setStyleSheet,
            'QToolButton {}'
        )

    @asyncify
    async def onProfileChanged(self, pName, profile):
        self.profileButton.setEnabled(False)

        if not profile.initialized:
            return

        def hashmarksLoaded(nodeId, hmarks):
            try:
                self.hashmarksSearcher.register(nodeId, hmarks)
            except Exception as err:
                log.debug(str(err))

        profile.sharedHManager.hashmarksLoaded.connect(hashmarksLoaded)

        await profile.userInfo.loaded
        self.profileButton.setEnabled(True)

        await self.profileButton.changeProfile(profile)

        for action in self.profilesActionGroup.actions():
            if action.data() == pName:
                action.setChecked(True)
                # Refresh the file manager
                tab = self.findTabFileManager()

                if tab:
                    tab.fileManager.setupModel()
                    tab.fileManager.pathSelectorDefault()

    def onProfilesList(self, pList):
        currentList = [action.data() for action in
                       self.profilesActionGroup.actions()]

        for pName in pList:
            if pName in currentList:
                continue

            action = QAction(self.profilesActionGroup,
                             checkable=True, text=pName)
            action.setData(pName)

        for action in self.profilesActionGroup.actions():
            self.menuUserProfile.addAction(action)

    async def onRepoReady(self):
        self.browseButton.normalIcon()

        self.fileManagerWidget.setupModel()

        await self.displayConnectionInfo()
        await self.app.marksLocal.pyramidsInit()
        await self.app.sqliteDb.feeds.start()
        await self.hashmarkMgrButton.updateMenu()
        await self.hashmarkMgrButton.updateIcons()
        await self.qaToolbar.init()

        self.enableButtons()

    @ipfsOp
    async def orbitStart(self, ipfsop):
        resp = await self.app.ipfsCtx.orbitConnector.start()

        if resp:
            orbitIcon = QPushButton()
            orbitIcon.setIcon(getIcon('orbitdb.png'))
            orbitIcon.setToolTip('OrbitDB: connected')
            self.statusbar.addPermanentWidget(orbitIcon)

            self.orbitManager = DatabasesManager(
                self.app.ipfsCtx.orbitConnector, self)
            self.toolbarTools.addWidget(self.orbitManager.button)

            await ipfsop.ctx.currentProfile.orbitalSetup(
                self.app.ipfsCtx.orbitConnector)

    def onSystrayMsgClicked(self):
        # Open last shown mark in the systray
        if self._lastFeedMark is not None:
            self.addBrowserTab().browseFsPath(self._lastFeedMark.path)

    async def onFeedMarkAdded(self, feed, mark):
        self._lastFeedMark = mark

        message = """New entry in IPNS feed {feed}: {title}.
                 Click the message to open it""".format(
            feed=feed.name,
            title=mark.title if mark.title else 'No title'
        )

        self.app.systemTrayMessage('IPNS', message, timeout=10000)

    def onIpfsInfosHovered(self, hovered):
        if hovered is False:
            return

        statusCubePos = self.ipfsStatusCube.mapToGlobal(self.pos())

        popupPoint = QPoint(
            statusCubePos.x() - self.ipfsDaemonStatusWidget.width() -
            self.ipfsStatusCube.width(),
            statusCubePos.y() - self.ipfsDaemonStatusWidget.height() -
            (2 * self.ipfsStatusCube.height())
        )

        self.ipfsDaemonStatusWidget.move(popupPoint)
        self.ipfsDaemonStatusWidget.setVisible(hovered)
        ensure(self.ipfsDaemonStatusWidget.update())

    async def onConnReady(self):
        pass

    def onPubsubRx(self):
        now = QDateTime.currentDateTime()
        self.pubsubStatusButton.setIcon(getIcon('network-transmit.png'))
        self.pubsubStatusButton.setToolTip(
            'Pubsub: last message received {}'.format(now.toString()))

    def onPubsubTx(self):
        pass

    def showPinningStatusWidget(self):
        name = self.tabnPinning
        ft = self.findTabWithName(name)
        if ft:
            return self.tabWidget.setCurrentWidget(ft)

        tab = self.pinStatusTab
        self.registerTab(tab, name, current=True,
                         icon=getIcon('pin-zoom.png'))

    def onPinItemsCount(self, count):
        statusMsg = iItemsInPinningQueue(count)

        if count > 0:
            self.pinningStatusButton.setIcon(self.pinIconLoading)
        else:
            self.pinningStatusButton.setIcon(self.pinIconNormal)

        self.pinningStatusButton.setToolTip(statusMsg)
        self.pinningStatusButton.setStatusTip(statusMsg)

    def onPinFinished(self, path):
        pass

    def onPinStatusChanged(self, qname, path, status):
        pass

    def onManualAvailable(self, langCode, entry):
        lang = iLangEnglish() if langCode == 'en' else iUnknown()
        self.menuManual.addAction(lang, functools.partial(
                                  self.onOpenManual, langCode))

    def onOpenManual(self, lang):
        entry = self.app.manuals.getManualEntry(lang)
        if entry:
            self.addBrowserTab().browseIpfsHash(entry['Hash'])

    def enableButtons(self, flag=True):
        for btn in [
                self.clipboardManager,
                self.browseButton,
                self.fileManagerButton,
                self.peersButton,
                self.textEditorButton,
                self.atomButton,
                self.chatCenterButton,
                self.cameraController,
                self.hashmarkMgrButton,
                self.hashmarksSearcher,
                self.profileButton]:
            btn.setEnabled(flag)

    def statusMessage(self, msg):
        self.lastLogLabel.setText(msg)
        self.lastLogLabel.setToolTip(msg)

    def registerTab(self, tab, name, icon=None, current=False,
                    tooltip=None):
        idx = None
        if icon:
            idx = self.tabWidget.addTab(tab, icon, name)
        else:
            idx = self.tabWidget.addTab(tab, name)

        self._allTabs.append(tab)

        if current is True:
            self.tabWidget.setCurrentWidget(tab)
            tab.setFocus(Qt.OtherFocusReason)

        if tooltip and idx:
            self.tabWidget.setTabToolTip(idx, tooltip)

    def findTabFileManager(self):
        return self.findTabWithName(self.tabnFManager)

    def findTabIndex(self, w):
        return self.tabWidget.indexOf(w)

    def findTabWithName(self, name):
        for idx in range(0, self.tabWidget.count()):
            tName = self.tabWidget.tabText(idx)

            if tName.strip() == name.strip():
                return self.tabWidget.widget(idx)

    def removeTabFromWidget(self, w):
        idx = self.tabWidget.indexOf(w)
        if idx:
            self.tabWidget.removeTab(idx)

    def onSettings(self):
        runDialog(settings.SettingsDialog, self.app)

    def onCloseAllTabs(self):
        self.tabWidget.clear()

    def onToggledPinAllGlobal(self, checked):
        self.pinAllGlobalChecked = checked

    def onAboutGalacteek(self):
        runDialog(AboutDialog, iAbout())

    def setConnectionInfoMessage(self, msg):
        self.app.systemTray.setToolTip('{app}: {msg}'.format(
            app=GALACTEEK_NAME, msg=msg))
        self.ipfsStatusCube.setToolTip(msg)

    @ipfsOp
    async def displayConnectionInfo(self, ipfsop):
        try:
            info = await ipfsop.client.core.id()
            bwStats = await ipfsop.client.stats.bw()
        except Exception:
            self.setConnectionInfoMessage(iErrNoCx())
            return

        nodeId = info.get('ID', iUnknown())
        nodeAgent = info.get('AgentVersion', iUnknownAgent())

        # Get IPFS peers list
        peers = await ipfsop.peersList()
        peersCount = len(peers)

        if peersCount == 0:
            await ipfsop.noPeersFound()
            self.setConnectionInfoMessage(iCxButNoPeers(nodeId, nodeAgent))
            self.ipfsStatusCube.clip.setSpeed(0)
            return

        await ipfsop.peersCountStatus(peersCount)

        # Notify the bandwidth graph with the stats
        if isinstance(bwStats, dict):
            await self.bwGraphView.bwStatsFetched.emit(bwStats)

        await self.peersGraphView.peersCountFetched.emit(peersCount)

        # TODO: compute something more precise, probably based on the
        # swarm's high/low config
        connQuality = int((peersCount * 100) / 1000)

        self.setConnectionInfoMessage(
            iConnectStatus(nodeId, nodeAgent, peersCount))
        self.ipfsStatusCube.clip.setSpeed(5 + min(connQuality, 80))

    def stopTimers(self):
        self.timerStatus.stop()

    def onMainTimerStatus(self):
        ensure(self.displayConnectionInfo())

    def keyPressEvent(self, event):
        modifiers = event.modifiers()

        if modifiers & Qt.ControlModifier:
            if event.key() == Qt.Key_W:
                idx = self.tabWidget.currentIndex()
                ensure(self.onTabCloseRequest(idx))

        super(MainWindow, self).keyPressEvent(event)

    def explore(self, cid):
        ipfsPath = IPFSPath(cid, autoCidConv=True)
        if ipfsPath.valid:
            ensure(self.exploreIpfsPath(ipfsPath))
        else:
            messageBox(iInvalidInput())

    @ipfsOp
    async def exploreIpfsPath(self, ipfsop, ipfsPath):
        cid = await ipfsPath.resolve(ipfsop)
        if not cid:
            return messageBox(iCannotResolve(str(ipfsPath)))

        path = IPFSPath(cid, autoCidConv=True)
        tabName = path.shortRepr()

        tooltip = 'CID explorer: {0}'.format(cid)
        view = ipfsview.IPFSHashExplorerStack(self, cid)
        self.registerTab(view, tabName, current=True,
                         icon=getIcon('hash.png'), tooltip=tooltip)

    def addMediaPlayerTab(self):
        name = self.tabnMediaPlayer
        ft = self.findTabWithName(name)
        if ft:
            return ft
        tab = mediaplayer.MediaPlayerTab(self)

        if tab.playerAvailable():
            self.registerTab(tab, name, icon=getIcon('multimedia.png'),
                             current=True)
            return tab
        else:
            messageBox(mediaplayer.iPlayerUnavailable())

    def mediaPlayerQueue(self, path, playLast=False, mediaName=None):
        tab = self.addMediaPlayerTab()
        if tab:
            tab.queueFromPath(path, mediaName=mediaName, playLast=playLast)

    def mediaPlayerPlay(self, path, mediaName=None):
        tab = self.addMediaPlayerTab()
        if tab:
            tab.playFromPath(path, mediaName=mediaName)

    def onTabChanged(self, idx):
        tab = self.tabWidget.widget(idx)
        if tab:
            ensure(tab.onTabChanged())

    async def onTabCloseRequest(self, idx):
        tab = self.tabWidget.widget(idx)

        if tab not in self.allTabs:
            return False

        if await tab.onClose() is True:
            self.tabWidget.removeTab(idx)
            self.allTabs.remove(tab)
            del tab

    def openPsniffTab(self):
        self.registerTab(
            PubsubSnifferWidget(self), iPubSubSniff(), current=True)

    def addEditorTab(self, path=None, editing=True):
        tab = TextEditorTab(editing=editing, parent=self)

        if isinstance(path, IPFSPath) and path.valid:
            tab.editor.display(path)

        self.registerTab(tab, iTextEditor(),
                         icon=getIcon('text-editor.png'), current=True,
                         tooltip=str(path))

    def addIpfsSearchView(self):
        tab = ipfssearch.IPFSSearchTab(self)
        self.registerTab(tab, iIpfsSearch(), current=True,
                         icon=getIcon('search-engine.png'))
        tab.view.browser.setFocus(Qt.OtherFocusReason)

    def onOpenMediaPlayer(self):
        self.addMediaPlayerTab()

    def onOpenBrowserTabClicked(self, pinBrowsed=False):
        self.addBrowserTab(pinBrowsed=pinBrowsed)

    def onWriteNewDocumentClicked(self):
        w = textedit.AddDocumentWidget(self, parent=self.tabWidget)
        self.registerTab(w, 'New document', current=True)

    def onPeersMgrClicked(self):
        self.showPeersMgr(current=True)

    def onFileManagerClicked(self):
        name = self.tabnFManager

        icon = getIcon('folder-open.png')
        ft = self.findTabWithName(name)
        if ft:
            ft.fileManager.updateTree()
            return self.tabWidget.setCurrentWidget(ft)

        fileManagerTab = files.FileManagerTab(
            self.tabWidget, fileManager=self.fileManagerWidget)
        self.registerTab(fileManagerTab, name, current=True, icon=icon)
        fileManagerTab.fileManager.updateTree()

    def onIpfsKeysClicked(self):
        name = self.tabnKeys
        ft = self.findTabWithName(name)
        if ft:
            return self.tabWidget.setCurrentWidget(ft)

        keysTab = keys.KeysTab(self)
        self.registerTab(keysTab, name, current=True)

    def onHelpDonate(self):
        tab = self.app.mainWindow.addBrowserTab()
        tab.enterUrl(
            QUrl('https://github.com/sponsors/pinnaculum'))

    def addBrowserTab(self, label='No page loaded', pinBrowsed=False,
                      minProfile=None, current=True):
        icon = getIconIpfsIce()
        tab = browser.BrowserTab(self,
                                 minProfile=minProfile,
                                 pinBrowsed=pinBrowsed)
        self.registerTab(tab, label, icon=icon, current=current)

        if self.app.settingsMgr.isTrue(CFG_SECTION_BROWSER, CFG_KEY_GOTOHOME):
            tab.loadHomePage()
        else:
            tab.focusUrlZone()

        return tab

    def addEventLogTab(self, current=False):
        self.registerTab(eventlog.EventLogWidget(self), iEventLog(),
                         current=current)

    def showPeersMgr(self, current=False):
        # Peers mgr
        name = iPeers()

        ft = self.findTabWithName(name)
        if ft:
            return self.tabWidget.setCurrentWidget(ft)

        pMgr = peers.PeersManager(self, self.app.peersTracker)
        self.registerTab(pMgr, name, icon=getIcon('peers.png'),
                         current=current)

    def quit(self):
        # Qt and application exit
        self.saveUiSettings()
        ensure(self.app.exitApp())

    def saveUiSettings(self):
        self.app.settingsMgr.setSetting(
            CFG_SECTION_UI,
            CFG_KEY_MAINWINDOW_GEOMETRY,
            self.saveGeometry())
        self.app.settingsMgr.setSetting(
            CFG_SECTION_UI,
            CFG_KEY_MAINWINDOW_STATE,
            self.saveState())

        self.app.settingsMgr.setSetting(
            CFG_SECTION_UI,
            CFG_KEY_BROWSER_AUTOPIN,
            self.pinAllGlobalButton.isChecked())

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def addHashmarksTab(self):
        ft = self.findTabWithName(self.tabnHashmarks)
        if ft:
            return self.tabWidget.setCurrentWidget(ft)

        tab = WebTab(self.tabWidget)

        if self.hashmarksPage is None:
            self.hashmarksPage = HashmarksPage(self.app.marksLocal,
                                               self.app.marksNetwork,
                                               parent=tab)
        hview = DWebView(page=self.hashmarksPage, parent=tab)
        tab.attach(hview)

        self.registerTab(tab, iHashmarks(),
                         icon=getIcon('hashmarks.png'), current=True)

    def onOpenChatWidget(self):
        tab = self.findTabWithName(self.tabnChat)
        if tab:
            tab.focusMessage()
            return self.tabWidget.setCurrentWidget(tab)

        self.registerTab(self.chatRoomWidget, self.tabnChat,
                         icon=getIcon('chat.png'), current=True)

    def onShowAtomFeeds(self):
        ft = self.findTabWithName(self.tabnFeeds)
        if ft:
            return self.tabWidget.setCurrentWidget(ft)

        tab = AtomFeedsViewTab(self, view=self.atomFeedsViewWidget)
        self.registerTab(tab, self.tabnFeeds,
                         icon=getIcon('atom-feed.png'), current=True)

    def onIpfsObjectServed(self, ipfsPath, cType, reqTime):
        # TODO
        # Called when an object was served by the native IPFS scheme handler
        pass

    def onSearchServices(self):
        self.pSearchDock.searchMode()

    def getPyrDropButtonFor(self, ipfsPath, origin=None):
        return self.toolbarPyramids.getPyrDropButtonFor(
            ipfsPath, origin=origin)
