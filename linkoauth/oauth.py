# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Raindrop.
#
# The Initial Developer of the Original Code is
# Mozilla Messaging, Inc..
# Portions created by the Initial Developer are Copyright (C) 2009
# the Initial Developer. All Rights Reserved.
#
# Contributor(s): Tarek Ziade <tarek@mozilla.com>
#
"""
based on code from velruse
"""

import urlparse
try:
    from urlparse import parse_qs
except ImportError:
    from cgi import parse_qs   # NOQA
import logging

import oauth2 as oauth

from linkoauth.util import config, redirect, asbool, build_url
from linkoauth.protocap import HttpRequestor
from linkoauth.errors import BadVersionError, AccessException


log = logging.getLogger("oauth.base")


def get_oauth_config(provider):
    key = 'oauth.%s.' % provider
    keylen = len(key)
    d = {}
    for k, v in config.items():
        if k.startswith(key):
            d[k[keylen:]] = v
    return d


class OAuth1(object):
    def __init__(self, provider):
        self.provider = provider
        self.config = get_oauth_config(provider)
        self.request_token_url = self.config.get('request')
        self.access_token_url = self.config.get('access')
        self.authorization_url = self.config.get('authorize')
        self.version = int(self.config.get('version', '1'))
        self.scope = self.config.get('scope', None)
        if self.version != 1:
            raise BadVersionError(self.version)
        self.consumer_key = self.config.get('consumer_key')
        self.consumer_secret = self.config.get('consumer_secret')
        self.consumer = oauth.Consumer(self.consumer_key, self.consumer_secret)
        self.sigmethod = oauth.SignatureMethod_HMAC_SHA1()

    def request_access(self, request, url, session):
        # Create the consumer and client, make the request
        client = oauth.Client(self.consumer)
        params = {'oauth_callback': url(controller='account',
                                        action="verify",
                                        provider=self.provider,
                                        qualified=True)}
        if self.scope:
            params['scope'] = self.scope

        # We go through some shennanigans here to specify a callback url
        oauth_request = oauth.Request.from_consumer_and_token(self.consumer,
            http_url=self.request_token_url, parameters=params)
        oauth_request.sign_request(self.sigmethod, self.consumer, None)
        client = HttpRequestor()

        resp, content = client.request(self.request_token_url, method='GET',
            headers=oauth_request.to_header())

        if resp['status'] != '200':
            client.save_capture("oauth1 request_access failure")
            raise AccessException("Error status: %r", resp['status'])

        request_token = oauth.Token.from_string(content)

        session['token'] = content
        session.save()

        # force_login is twitter specific
        if (self.provider == 'twitter.com' and
            asbool(request.POST.get('force_login'))):
            http_url = self.authorization_url + '?force_login=true'
        else:
            http_url = self.authorization_url

        # Send the user to the oauth provider to authorize us
        oauth_request = \
                oauth.Request.from_token_and_callback(token=request_token,
                                                      http_url=http_url)
        return redirect(oauth_request.to_url())

    def verify(self, request, url, session):
        request_token = oauth.Token.from_string(session['token'])
        verifier = request.GET.get('oauth_verifier')
        if not verifier:
            redirect(self.config.get('oauth_failure'))

        request_token.set_verifier(verifier)
        client = oauth.Client(self.consumer, request_token)
        resp, content = client.request(self.access_token_url, "POST")
        if resp['status'] != '200':
            redirect(self.config.get('oauth_failure'))

        access_token = dict(urlparse.parse_qsl(content))
        return self._get_credentials(access_token)

    def _get_credentials(self, access_token):
        return access_token


class OAuth2(object):
    def __init__(self, provider):
        self.provider = provider
        self.config = get_oauth_config(provider)
        self.access_token_url = self.config.get('access')
        self.authorization_url = self.config.get('authorize')
        self.version = int(self.config.get('version', '2'))
        if self.version != 2:
            raise BadVersionError(self.version)
        self.app_id = self.config.get('app_id')
        self.app_secret = self.config.get('app_secret')
        self.scope = self.config.get('scope', None)

    def request_access(self, request, url, session):
        return_to = url(controller='account', action="verify",
                        provider=self.provider,
                        qualified=True)

        loc = build_url(self.authorization_url, client_id=self.app_id,
                        scope=self.scope,
                        redirect_uri=return_to)
        return redirect(loc)

    def verify(self, request, url, session):
        code = request.GET.get('code')
        if not code:
            error = request.params.get('error', '')
            reason = request.params.get('error_reason', '')
            desc = request.params.get('error_description',
                                      'No oauth code received')
            err = "%s - %s - %s" % (error, reason, desc)
            log.info("%s: %s", self.provider, err)
            raise AccessException(err)

        return_to = url(controller='account', action="verify",
                        provider=self.provider, qualified=True)

        access_url = build_url(self.access_token_url, client_id=self.app_id,
                client_secret=self.app_secret, code=code,
                redirect_uri=return_to)

        client = HttpRequestor()
        resp, content = client.request(access_url)
        if resp['status'] != '200':
            client.save_capture("oauth2 verify failure")
            raise Exception("Error status: %s" % (resp['status'],))

        access_token = parse_qs(content)['access_token'][0]
        return self._get_credentials(access_token)

    def _get_credentials(self, access_token):
        return access_token
