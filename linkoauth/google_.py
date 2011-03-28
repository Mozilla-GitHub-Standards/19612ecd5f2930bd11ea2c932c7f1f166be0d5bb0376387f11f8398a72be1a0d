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
#

"""Google Responder

A Google responder that authenticates against Google using OpenID,
or optionally can use OpenId+OAuth hybrid protocol to request access to
Google Apps using OAuth2.

"""
import os
import urlparse
from openid.extensions import ax, pape
from openid.consumer import consumer
from openid import oidutil
import json
import logging
log = logging.getLogger(__name__)

from openid.consumer.discover import DiscoveryFailure
from openid.message import OPENID_NS, OPENID2_NS

import oauth2 as oauth
#from oauth2.clients.smtp import SMTP
import smtplib
import base64
from rfc822 import AddressList
import gdata.contacts

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.header import Header

from linkoauth.util import config, asbool, render
from linkoauth.util import safeHTML, literal
from linkoauth.oid_extensions import OAuthRequest
from linkoauth.oid_extensions import UIRequest
from linkoauth.openidconsumer import ax_attributes, attributes
from linkoauth.openidconsumer import OpenIDResponder
from linkoauth.base import get_oauth_config, OAuthKeysException
from linkoauth.protocap import ProtocolCapturingBase, OAuth2Requestor

GOOGLE_OAUTH = 'https://www.google.com/accounts/OAuthGetAccessToken'

domain = 'google.com'


class GoogleConsumer(consumer.GenericConsumer):
    # a HACK to allow us to user google domains for federated login.
    # this doesn't do the proper discovery and validation, but since we
    # are forcing this to go through well known endpoints it is fine.
    def _discoverAndVerify(self, claimed_id, to_match_endpoints):
        oidutil.log('Performing discovery on %s' % (claimed_id,))
        if not claimed_id.startswith('https://www.google.com/accounts/'):
            # want to get a service endpoint for the domain, but keep the
            # original claimed_id so tests during verify pass
            g_claimed_id = \
              "https://www.google.com/accounts/o8/user-xrds?uri=" + claimed_id
            _, services = self._discover(g_claimed_id)
            services[0].claimed_id = claimed_id
        else:
            _, services = self._discover(claimed_id)
        if not services:
            raise DiscoveryFailure('No OpenID information found at %s' %
                                   (claimed_id,), None)
        return self._verifyDiscoveredServices(claimed_id, services,
                                              to_match_endpoints)

    def complete(self, message, endpoint, return_to):
        """Process the OpenID message, using the specified endpoint
        and return_to URL as context. This method will handle any
        OpenID message that is sent to the return_to URL.
        """
        mode = message.getArg(OPENID_NS, 'mode', '<No mode set>')
        claimed_id = message.getArg(OPENID2_NS, 'claimed_id')
        if not claimed_id.startswith('https://www.google.com/accounts/'):
            # we want to be sure we have the correct endpoint with the
            # google domain claimed_id hacked in
            claimed_id = \
              "https://www.google.com/accounts/o8/user-xrds?uri=" + claimed_id
            _, services = self._discover(claimed_id)
            endpoint = services[0]
        modeMethod = getattr(self, '_complete_' + mode,
                             self._completeInvalid)

        return modeMethod(message, endpoint, return_to)


class responder(OpenIDResponder):
    def __init__(self, consumer=None, oauth_key=None, oauth_secret=None,
                 request_attributes=None, domain='google.com',
                 *args, **kwargs):
        """Handle Google Auth

        This also handles making an OAuth request during the OpenID
        authentication.

        """

        OpenIDResponder.__init__(self, domain)
        self.consumer_key = str(self.config.get('consumer_key'))
        self.consumer_secret = str(self.config.get('consumer_secret'))
        # support for google apps domains
        self.provider = domain
        self.consumer_class = GoogleConsumer

    def _lookup_identifier(self, identifier):
        """Return the Google OpenID directed endpoint"""
        if identifier:
            return \
             "https://www.google.com/accounts/o8/site-xrds?hd=%s" % identifier
        return "https://www.google.com/accounts/o8/id"

    def _update_authrequest(self, authrequest, request):
        """Update the authrequest with Attribute Exchange and optionally OAuth

        To optionally request OAuth, the request POST must include an
        ``oauth_scope``
        parameter that indicates what Google Apps should have access requested.

        """
        request_attributes = request.POST.get('ax_attributes',
                ax_attributes.keys())
        ax_request = ax.FetchRequest()
        for attr in request_attributes:
            ax_request.add(ax.AttrInfo(attributes[attr], required=True))
        authrequest.addExtension(ax_request)

        # Add PAPE request information.
        # Setting max_auth_age to zero will force a login.
        requested_policies = []
        policy_prefix = 'policy_'
        for k, v in request.POST.iteritems():
            if k.startswith(policy_prefix):
                policy_attr = k[len(policy_prefix):]
                requested_policies.append(getattr(pape, policy_attr))

        pape_request = pape.Request(requested_policies,
                     max_auth_age=request.POST.get('pape_max_auth_age', None))
        authrequest.addExtension(pape_request)

        oauth_request = OAuthRequest(consumer=self.consumer_key,
                                     scope=self.scope or
                                     'http://www.google.com/m8/feeds/')
        authrequest.addExtension(oauth_request)

        if 'popup_mode' in request.POST:
            kw_args = {'mode': request.POST['popup_mode']}
            if 'popup_icon' in request.POST:
                kw_args['icon'] = request.POST['popup_icon']
            ui_request = UIRequest(**kw_args)
            authrequest.addExtension(ui_request)
        return None

    def _update_verify(self, consumer):
        pass

    def _get_access_token(self, request_token):
        """Retrieve the access token if OAuth hybrid was used"""
        consumer = oauth.Consumer(self.consumer_key, self.consumer_secret)
        token = oauth.Token(key=request_token, secret='')
        client = oauth.Client(consumer, token)
        resp, content = client.request(GOOGLE_OAUTH, "POST")
        if resp['status'] != '200':
            return None
        return dict(urlparse.parse_qsl(content))

    def _get_credentials(self, result_data):
        #{'profile': {'preferredUsername': u'mixedpuppy',
        #     'displayName': u'Shane Caraveo',
        #     'name':
        #        {'givenName': u'Shane',
        #         'formatted': u'Shane Caraveo',
        #         'familyName': u'Caraveo'},
        #        'providerName': 'Google',
        #        'verifiedEmail': u'mixedpuppy@gmail.com',
        #'identifier':
        #'https://www.google.com/accounts/o8/id?
        #               id=AItOawnEHbJcEY5EtwX7vf81_x2P4KUjha35VyQ'}}

        # google OpenID for domains result is:
        #{'profile': {
        #    'displayName': u'Shane Caraveo',
        #    'name': {'givenName': u'Shane',
        #             'formatted': u'Shane Caraveo',
        #             'familyName': u'Caraveo'},
        #    'providerName': 'OpenID',
        #    'identifier':
        #       u'http://g.caraveo.com/openid?id=103543354513986529024',
        #    'emails': [u'mixedpuppy@g.caraveo.com']}}

        profile = result_data['profile']
        provider = domain
        if profile.get('providerName').lower() == 'openid':
            provider = 'googleapps.com'
        userid = profile.get('verifiedEmail', '')
        emails = profile.get('emails')
        profile['emails'] = []
        if userid:
            profile['emails'] = [{'value': userid, 'primary': False}]
        if emails:
            # fix the emails list
            for e in emails:
                profile['emails'].append({'value': e, 'primary': False})
        profile['emails'][0]['primary'] = True
        account = {'domain': provider,
                   'userid': profile['emails'][0]['value'],
                   'username': profile.get('preferredUsername', '')}
        profile['accounts'] = [account]
        return result_data


# XXX right now, python-oauth2 does not raise the exception if there is an
# error, this is copied from oauth2.clients.smtp and fixed
class SMTP(smtplib.SMTP):
    """SMTP wrapper for smtplib.SMTP that implements XOAUTH."""

    def authenticate(self, url, consumer, token):
        if consumer is not None and not isinstance(consumer, oauth.Consumer):
            raise ValueError("Invalid consumer.")

        if token is not None and not isinstance(token, oauth.Token):
            raise ValueError("Invalid token.")

        xoauth_string = oauth.build_xoauth_string(url, consumer, token)
        code, resp = self.docmd('AUTH',
                                'XOAUTH %s' % base64.b64encode(xoauth_string))
        if code >= 500:
            raise smtplib.SMTPResponseException(code, resp)
        return code, resp

# A "protocol capturing" SMTP class - should move into its own module
# once we get support for other SMTP servers...
class SMTPRequestorImpl(SMTP, ProtocolCapturingBase):
    pc_protocol = "smtp"
    def __init__(self, host, port):
        self._record = []
        self.pc_host = host
        SMTP.__init__(self, host, port)
        ProtocolCapturingBase.__init__(self)

    def pc_get_host(self):
        return self.pc_host

    def send(self, str):
        msg = "> " + "\n+ ".join(str.splitlines()) + "\n"
        self._record.append(msg)
        SMTP.send(self, str)

    def getreply(self):
        try:
            errcode, errmsg = SMTP.getreply(self)
        except Exception, exc:
            try:
                module = getattr(exc, '__module__', None)
                erepr = {'module': module, 'name': exc.__class__.__name__, 'args': exc.args}
                self._record.append("E " + json.dumps(erepr))
            except Exception:
                log.exception("failed to serialize an SMTP exception")
            raise

        msg = "\n+ ".join(errmsg.splitlines()) + "\n"
        self._record.append("< %d %s" % (errcode, msg))
        return errcode, errmsg

    def sendmail(self, *args, **kw):
        SMTP.sendmail(self, *args, **kw)
        if asbool(config.get('protocol_capture_success')):
            self.save_capture("automatic success save")

    def _save_capture(self, dirname):
        with open(os.path.join(dirname, "smtp-trace"), "wb") as f:
            f.writelines(self._record)
        self._record = []
        return None

SMTPRequestor = SMTPRequestorImpl

class api():
    def __init__(self, account):
        self.host = "smtp.gmail.com"
        self.port = 587
        self.config = get_oauth_config(domain)
        self.account = account
        try:
            self.oauth_token = oauth.Token(
                    key=account.get('oauth_token'),
                    secret=account.get('oauth_token_secret'))
        except ValueError, e:
            # missing oauth tokens, raise our own exception
            raise OAuthKeysException(str(e))
        self.consumer_key = str(self.config.get('consumer_key'))
        self.consumer_secret = str(self.config.get('consumer_secret'))
        self.consumer = oauth.Consumer(key=self.consumer_key,
                secret=self.consumer_secret)

    def sendmessage(self, message, options={}):
        result = error = None

        profile = self.account.get('profile', {})
        from_email = from_ = profile['emails'][0]['value']
        fullname = profile.get('displayName', None)
        if fullname:
            from_email = '"%s" <%s>' % (Header(fullname, 'utf-8').encode(),
                                        Header(from_, 'utf-8').encode())

        url = "https://mail.google.com/mail/b/%s/smtp/" % from_
        # 'to' parsing
        address_list = AddressList(options.get('to', ''))
        if len(address_list) == 0:
            return None, {
                "provider": self.host,
                "message": "recipient address must be specified",
                "status": 0}
        to_headers = []
        for addr in address_list:
            if not addr[1] or not '@' in addr[1]:
                return None, {
                    "provider": self.host,
                    "message": "recipient address '%s' is invalid" % addr[1],
                    "status": 0}
            if addr[0]:
                to_ = '"%s" <%s>' % (Header(addr[0], 'utf-8').encode(),
                                     Header(addr[1], 'utf-8').encode())
            else:
                to_ = Header(addr[1], 'utf-8').encode()
            to_headers.append(to_)
        assert to_headers  # we caught all cases where it could now be empty.

        subject = options.get('subject', config.get('share_subject',
                              'A web link has been shared with you'))
        title = options.get('title', options.get('link',
                                                 options.get('shorturl', '')))
        description = options.get('description', '')[:280]

        msg = MIMEMultipart('alternative')
        msg.set_charset('utf-8')
        msg.add_header('Subject', Header(subject, 'utf-8').encode())
        msg.add_header('From', from_email)
        for to_ in to_headers:
            msg.add_header('To', to_)

        extra_vars = {'safeHTML': safeHTML,
                      'options': options}

        # insert the url if it is not already in the message
        extra_vars['longurl'] = options.get('link')
        extra_vars['shorturl'] = options.get('shorturl')

        # reset to unwrapped for html email, they will be escaped
        extra_vars['from_name'] = fullname
        extra_vars['subject'] = subject
        extra_vars['from_header'] = from_
        extra_vars['title'] = title
        extra_vars['description'] = description
        extra_vars['message'] = message
        extra_vars['thumbnail'] = options.get('picture_base64', "") != ""

        mail = render('/html_email.mako', extra_vars=extra_vars)
        mail = mail.encode('utf-8')

        if extra_vars['thumbnail']:
            part2 = MIMEMultipart('related')
            html = MIMEText(mail, 'html')
            html.set_charset('utf-8')

            # FIXME: we decode the base64 data just so MIMEImage
            # can re-encode it as base64
            image = MIMEImage(base64.b64decode(options.get('picture_base64')),
                                               'png')
            image.add_header('Content-Id', '<thumbnail>')
            image.add_header('Content-Disposition',
                             'inline; filename=thumbnail.png')

            part2.attach(html)
            part2.attach(image)
        else:
            part2 = MIMEText(mail, 'html')
            part2.set_charset('utf-8')

        # get the title, or the long url or the short url or nothing
        # wrap these in literal for text email
        extra_vars['from_name'] = literal(fullname)
        extra_vars['subject'] = literal(subject)
        extra_vars['from_header'] = literal(from_)
        extra_vars['title'] = literal(title)
        extra_vars['description'] = literal(description)
        extra_vars['message'] = literal(message)

        rendered = render('/text_email.mako', extra_vars=extra_vars)
        part1 = MIMEText(rendered.encode('utf-8'), 'plain')
        part1.set_charset('utf-8')

        msg.attach(part1)
        msg.attach(part2)

        server = None
        try:
            server = SMTPRequestor(self.host, self.port)
            # in the app:main set debug = true to enable
            if asbool(config.get('debug', False)):
                server.set_debuglevel(True)
            try:
                try:
                    server.starttls()
                except smtplib.SMTPException:
                    log.info("smtp server does not support TLS")
                try:
                    server.ehlo_or_helo_if_needed()
                    server.authenticate(url, self.consumer, self.oauth_token)
                    server.sendmail(from_, to_headers, msg.as_string())
                except smtplib.SMTPRecipientsRefused, exc:
                    server.save_capture("rejected recipients")
                    for to_, err in exc.recipients.items():
                        error = {"provider": self.host,
                                 "message": err[1],
                                 "status": err[0]}
                        break
                except smtplib.SMTPResponseException, exc:
                    server.save_capture("smtp response exception")
                    error = {"provider": self.host,
                             "message": "%s: %s" % (exc.smtp_code, exc.smtp_error),
                             "status": exc.smtp_code}
                except smtplib.SMTPException, exc:
                    server.save_capture("smtp exception")
                    error = {"provider": self.host,
                             "message": str(exc)}
                except UnicodeEncodeError, exc:
                    server.save_capture("unicode error")
                    raise
                except ValueError, exc:
                    server.save_capture("ValueError sending email")
                    error = {"provider": self.host,
                             "message": str(exc)}
            finally:
                try:
                    server.quit()
                except smtplib.SMTPServerDisconnected:
                    # an error above may have already disconnected, so we can
                    # ignore the error while quiting.
                    pass
        except smtplib.SMTPResponseException, exc:
            if server is not None:
                server.save_capture("early smtp response exception")
            error = {"provider": self.host,
                     "message": "%s: %s" % (exc.smtp_code, exc.smtp_error),
                     "status": exc.smtp_code}
        except smtplib.SMTPException, exc:
            if server is not None:
                server.save_capture("early smtp exception")
            error = {"provider": self.host,
                     "message": str(exc)}
        if error is None:
            result = {"status": "message sent"}
        return result, error

    def getgroup_id(self, group):
        url = 'https://www.google.com/m8/feeds/groups/default/full?v=2'
        method = 'GET'
        client = oauth.Client(self.consumer, self.oauth_token)
        resp, content = client.request(url, method)
        feed = gdata.contacts.GroupsFeedFromString(content)
        for entry in feed.entry:
            this_group = entry.content.text
            if this_group.startswith('System Group: '):
                this_group = this_group[14:]
            if group == this_group:
                return entry.id.text

    def getcontacts(self, start=0, page=25, group=None):
        contacts = []
        userdomain = 'default'

        # google domains can have two contacts lists, the users and the domains
        # shared contacts.
        # shared contacts are only available in paid-for google domain accounts
        # and do not show the users full contacts list.  I also did not find
        # docs on how to detect whether shared contacts is available or not,
        # so we will bypass this and simply use the users contacts list.
        #profile = self.account.get('profile', {})
        #accounts = profile.get('accounts', [{}])
        #if accounts[0].get('domain') == 'googleapps.com':
        #    # set the domain so we get the shared contacts
        #    userdomain = accounts[0].get('userid').split('@')[-1]

        url = ('http://www.google.com/m8/feeds/contacts/%s/full?'
               'v=1&orderby=lastmodified&sortorder=descending'
               '&max-results=%d') % (userdomain, page)

        method = 'GET'
        if start > 0:
            url = url + "&start-index=%d" % (start,)
        if group:
            gid = self.getgroup_id(group)
            if not gid:
                error = {"provider": domain,
                         "message": "Group '%s' not available" % group}
                return None, error
            url = url + "&group=%s" % (gid,)

        # itemsPerPage, startIndex, totalResults
        requestor = OAuth2Requestor(self.consumer, self.oauth_token)
        resp, content = requestor.request(url, method)

        if int(resp.status) != 200:
            requestor.save_capture("contact fetch failure")
            error = {"provider": domain,
                     "message": content,
                     "status": int(resp.status)}
            return None, error

        feed = gdata.contacts.ContactsFeedFromString(content)
        for entry in feed.entry:
            #print entry.group_membership_info
            if entry.email:
                p = {'displayName': entry.title.text, 'emails': []}

                for email in entry.email:
                    p['emails'].append({'value': email.address,
                                        'primary': email.primary})
                    if not p['displayName']:
                        p['displayName'] = email.address
                contacts.append(p)
        result = {
            'entry': contacts,
            'itemsPerPage': feed.items_per_page.text,
            'startIndex':   feed.start_index.text,
            'totalResults': feed.total_results.text,
        }
        return result, None
