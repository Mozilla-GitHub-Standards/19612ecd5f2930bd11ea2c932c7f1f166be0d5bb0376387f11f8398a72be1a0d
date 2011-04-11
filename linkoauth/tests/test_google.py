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
# Contributor(s):
#       Tarek Ziade <tarek@mozilla.com>
import socket
import unittest
import httplib2
import json
import urllib2

from linkoauth.util import setup_config
from linkoauth import get_requester
from linkoauth import google_


_ACCOUNT = {'oauth_token': 'xxx',
            'oauth_token_secret': 'xxx',
            'profile': {'emails':
                        [{'value': 'tarek@ziade.org'}]}}

_CONFIG = {'oauth.yahoo.com.consumer_key': 'xxx',
           'oauth.yahoo.com.consumer_secret': 'xxx',
           'oauth.linkedin.com.consumer_key': 'xxx',
           'oauth.linkedin.com.consumer_secret': 'xxx',
           'oauth.twitter.com.consumer_key': 'xxx',
           'oauth.twitter.com.consumer_secret': 'xxx'}


class _Res(dict):
    def __init__(self, status):
        self.status = status
        self['status'] = status


def _request(*args, **kwargs):
    res = {'status': 200, 'id': 123, 'error': '',
            'result': {'status': 200}}
    return _Res(200), json.dumps(res)


class _SMTP(object):

    working = True

    def __init__(self, *args):
        pass

    def quit(self):
        pass

    ehlo_or_helo_if_needed = starttls = quit

    def authenticate(self, *args):
        if not self.working:
            raise socket.timeout()

    sendmail = authenticate

    def save_capture(self, msg):
        pass


class _FakeResult(object):

    headers = {}

    def read(self):
        res = {'id': 123, 'status': 200}
        return json.dumps(res)


def _urlopen(*args):
    return _FakeResult()


class TestBasics(unittest.TestCase):

    def setUp(self):
        setup_config(_CONFIG)
        self.old_httplib2 = httplib2.Http.request
        httplib2.Http.request = _request
        self.old_smtp = google_.SMTP
        google_.SMTPRequestor = _SMTP
        self.old_urlopen = urllib2.urlopen
        urllib2.urlopen = _urlopen

    def tearDown(self):
        httplib2.Http.request = self.old_httplib2
        google_.SMTPRequestor = self.old_smtp
        urllib2.urlopen = self.old_urlopen

    def test_callbacks(self):
        message = ''
        args = {'to': 'tarek@ziade.org',
                'subject': 'xxx',
                'title': 'the title',
                'description': 'some description',
                'link': 'http://example.com',
                'shorturl': 'http://example.com'}

        results = {'google.com': {'succ': 0, 'fail': 0}}

        def callback(domain, res):
            if res:
                results[domain]['succ'] += 1
            else:
                results[domain]['fail'] += 1

        google = get_requester('google.com', _ACCOUNT,
                               status_callback=callback)

        # this sends a success to the callback
        res, error = google.sendmessage(message, args)
        self.assertEquals(results['google.com']['succ'], 1)

        # let's break SMTP
        _SMTP.working = False
        try:
            res, error = google.sendmessage(message, args)
        finally:
            _SMTP.working = True

        self.assertEquals(results['google.com']['fail'], 1)