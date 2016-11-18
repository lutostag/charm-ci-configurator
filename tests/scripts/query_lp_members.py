#! /usr/bin/env python
# Copyright (C) 2011 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# Synchronize Gerrit users from Launchpad.

import os
import re
import sys
import yaml
import urllib2

from launchpadlib.launchpad import Launchpad
from launchpadlib.uris import LPNET_SERVICE_ROOT
from lazr.restfulclient.errors import Unauthorized

from openid.consumer import consumer
from openid.cryptutil import randomString
sys.path.append(os.path.abspath(os.path.dirname(__file__) + '/' + '../hooks'))

from gerrit import *

GERRIT_CACHE_DIR = LAUNCHPAD_DIR+'/cache'
GERRIT_CREDENTIALS = LAUNCHPAD_DIR+'/creds'

# check parameters from command line
if len(sys.argv) < 3:
    print "ERROR: Please send user and private key in parameters."
    sys.exit(1)

admin_username = sys.argv[1]
admin_privkey = sys.argv[2]

for check_path in (os.path.dirname(GERRIT_CACHE_DIR),
                   os.path.dirname(GERRIT_CREDENTIALS)):
    if not os.path.exists(check_path):
        os.makedirs(check_path)


def get_type(in_type):
    if in_type == "RSA":
        return "ssh-rsa"
    else:
        return "ssh-dsa"


launchpad = Launchpad.login_with('Canonical CI Gerrit User Sync',
                                 LPNET_SERVICE_ROOT,
                                 GERRIT_CACHE_DIR,
                                 credentials_file=GERRIT_CREDENTIALS)


def get_openid(lp_user):
    k = dict(id=randomString(16, '0123456789abcdef'))
    openid_consumer = consumer.Consumer(k, None)
    openid_request = openid_consumer.begin(
        "https://launchpad.net/~%s" % lp_user)
    return openid_request.endpoint.getLocalID()

# create gerrit connection
gerrit_client = GerritClient(
    host='localhost',
    user=admin_username,
    port=SSH_PORT,
    key_file=admin_privkey)

groups_config = {}
with open(GROUPS_CONFIG_FILE, 'r') as f:
    groups_config = yaml.load(f)

NEED_FLUSH = False
SEEN_LOGINS = set()


def assert_is_valid_email(email):
    if not email or not re.search('.+?@.+?\..+?', email):
        msg = "invalid email address '%s'" % (email)
        raise Exception(msg)


# Recurse members_details to return a list of (final)users as a tuples:
# (login, full_name, email, ssh_keys, openid)
def get_all_users(members_details, team_name):
    users = []
    for detail in members_details:
        # detail.self_link ==
        # 'https://api.launchpad.net/1.0/~team/+member/${username}'
        login = detail.self_link.split('/')[-1]

        status = detail.status
        member = launchpad.people[login]

        if not (status == "Approved" or status == "Administrator"):
            continue
        # Avoid re-visiting SEEN_LOGINS
        if login in SEEN_LOGINS:
            print ("'%s' details already identified - skipping alternate" %
                   (login))
            continue

        print '{}-entry: {}/{}'.format('T' if member.is_team else 'U', team_name, login)

        # If is_team recurse down(branch), else add this user details(leaf) to users
        if member.is_team:
            try:
                users.extend(get_all_users(member.members_details, "{}/{}".format(team_name, member.name)))
            except Unauthorized:
                print "WARN: skipping team={}/{} (Unauthorized)".format(team_name, member.name)
                pass
        else:
            openid = get_openid(login)
            full_name = member.display_name.encode('ascii', 'replace')
            email = ''
            errmsg = ("failed to get valid email address for '%s' (%s) - "
                      "skipping")
            try:
                email = member.preferred_email_address.email
                assert_is_valid_email(email)
            except Exception as exc:
                print (errmsg % (login, str(exc)))
                continue
            except:
                # Do catchall just in case an exception is raised that does not
                # inherit Exception.
                print (errmsg % (login, 'no exception info available'))
                continue

            ssh_keys = tuple(
                "{} {} {}".format(get_type(key.keytype), key.keytext, key.comment).strip()
                for key in member.sshkeys
            )
            users.append((login, full_name, email, ssh_keys, openid),)

        # Only remember login if it was actually used.
        SEEN_LOGINS.add(login)

    # Return a list with user details tuple
    return users

for group, teams in groups_config.items():
    # create group if not exists
    try:
        print "Creating group %s" % group
        gerrit_client.create_group(group)
        NEED_FLUSH = True
    except:
        print "Skipping group creation"

    # grab all the users in that teams
    teams = teams.split(' ')

    final_users = []
    for team_todo in teams:
        team = launchpad.people[team_todo]
        print "Creating users for team %s" % team
        final_users.extend(get_all_users(team.members_details, team_todo))

    if final_users:
        NEED_FLUSH = True

    # add all the users
    try:
        gerrit_client.create_users_batch(group, final_users)
    except Exception as e:
        print "ERROR creating users %s" % str(e)
        sys.exit(1)

if NEED_FLUSH:
    gerrit_client.flush_cache()

# Workaround https://github.com/paramiko/paramiko/issues/17
gerrit_client.ssh.close()
