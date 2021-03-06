import json
import base64

from galacteek.did import normedUtcDate
from galacteek.did import didIdentRe
from galacteek.ipfs.tunnel import P2PListener
from galacteek.ipfs import ipfsOp
from galacteek.ipfs.p2pservices import P2PService
from galacteek.core import jsonSchemaValidate
from galacteek.crypto.rsa import RSAExecutor
from galacteek import log

from aiohttp import web


authReqSchema = {
    "title": "DID Auth challenge request",
    "description": "DID Auth challenge",
    "type": "object",
    "properties": {
        "did": {
            "type": "string"
        },
        "nonce": {
            "type": "string"
        },
        "challenge": {
            "type": "string"
        }
    },
    "required": ["did", "nonce", "challenge"]
}


class DIDAuthWebApp(web.Application):
    pass


class DIDAuthSiteHandler:
    def __init__(self):
        self.rsaExecutor = RSAExecutor()

    def message(self, msg, level='debug'):
        getattr(log, level)(msg)

    async def msgError(self, error='Invalid request', status=500):
        return web.json_response({
            'error': error
        }, status=status)

    @ipfsOp
    async def vcResponse(self, ipfsop, did, signature, nonce):
        """
        Serialize the Verifiable Credential

        claim.publicKey and proof.verificationMethod are fixed for now
        as we only use RSA and one key
        """

        # Load the context first
        vcResponse = await ipfsop.ldContextJson('VerifiableCredential')

        # Complete with the VC
        vcResponse.update({
            'type': 'VerifiableCredential',
            'issuer': did,
            'issued': normedUtcDate(),
            'issuanceDate': normedUtcDate(),
            'credentialSubject': {
                'id': did
            },
            'proof': {
                'type': 'RsaSignature2018',
                'created': normedUtcDate(),
                'proofPurpose': 'authentication',
                'verificationMethod': '{}#keys-1'.format(did),
                'challenge': nonce,
                'nonce': nonce,
                'proofValue': signature
            }
        })
        return web.json_response(vcResponse)

    @ipfsOp
    async def authPss(self, ipfsop, request):
        curProfile = ipfsop.ctx.currentProfile

        if not curProfile:
            return await self.msgError()

        try:
            js = await request.json()
            if not js or not isinstance(js, dict):
                return await self.msgError()

            if not jsonSchemaValidate(js, authReqSchema):
                raise Exception('Invalid req schema')
        except Exception:
            return await self.msgError()

        did = js.get('did')
        if not didIdentRe.match(did):
            return await self.msgError()

        self.message('Received DID auth challenge request for DID: {}'.format(
            did))

        currentIpid = await curProfile.userInfo.ipIdentifier()

        if not currentIpid or did != currentIpid.did:
            # We don't answer to requests for DIDs other than the
            # one we currently use
            return await self.msgError(error='Invalid DID')

        privKey = curProfile._didKeyStore._privateKeyForDid(did)
        if not privKey:
            return await self.msgError()

        try:
            signed = await self.rsaExecutor.pssSign(
                js['challenge'].encode(),
                privKey.exportKey()
            )

            if signed:
                return await self.vcResponse(
                    did,
                    base64.b64encode(signed).decode(),
                    js['nonce']
                )
        except Exception:
            return await self.msgError(error='PSS error')


class DIDAuthService(P2PService):
    def __init__(self):
        super().__init__(
            'didauth-vc-pss',
            'DID Auth service',
            'didauth-vc-pss',
            ('127.0.0.1', range(49442, 49452)),
            None
        )

    @ipfsOp
    async def createListener(self, ipfsop):
        self._listener = DIDAuthListener(
            self,
            ipfsop.client,
            self.protocolName,
            self.listenRange,
            None,
            loop=ipfsop.client.loop
        )
        addr = await self.listener.open()
        log.debug('DID Auth service: created listener at address {0}'.format(
            addr))
        return addr != None


class DIDAuthListener(P2PListener):
    async def createServer(self, host='127.0.0.1', portRange=[]):
        for port in portRange:
            try:
                self.app = DIDAuthWebApp()
                self.handler = DIDAuthSiteHandler()
                self.app.router.add_post('/auth', self.handler.authPss)

                server = await self.loop.create_server(
                    self.app.make_handler(debug=True), host, port)

                log.debug('DID Auth service (port: {port}): started'.format(
                    port=port))
                self._server = server
                return (host, port)
            except Exception:
                continue
