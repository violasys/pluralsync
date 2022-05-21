import json
import os
import sys
import subprocess
import time
from collections import namedtuple


class Credentials(namedtuple('Credentials', ['sp_token', 'pk_token', 'sp_userid'])):
    pass


def read_credentials():
    with open('credentials.txt', 'r') as f:
        data = json.loads(f.read().strip())
        return Credentials(**data)

creds = read_credentials()


print(f'''
    Credentials:
      SimplyPlural: {creds.sp_token}
      PluralKit:    {creds.pk_token}
''')


class CurlError(Exception):
    def __init__(self, message):
        super(self, message)


def curl(url, method="GET", headers=None, data=None):
    command = ['curl', '--fail', '-X', method]
    for name, value in (headers or {}).items():
        command.extend(['-H', f'{name}: {value}'])
    if data:
        command.extend(['-d', data])
    command.append(url)
    for attempt in range(6):
        # the --fail arg will make curl fail on server errors
        print(method, url)
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        if p.returncode == 0:
            return out.strip()
            break
        print(out)
        print(err)
        # exponential backoff
        delay = 2 ** attempt
        print(f'... server failed, retrying in {delay} seconds.')
        time.sleep(delay)


class SimplyPluralApi:
    class Member:
        def __init__(self, data):
            self._data = data

        def _c(self, key):
            return self._data.get('content').get(key)

        def __repr__(self):
            return json.dumps({
                key: getattr(self, key) for key in dir(self) if not key.startswith('_')
            }, indent=2)

        @property
        def exists(self):
            return self._data.get('exists')

        @property
        def id(self):
            return self._data.get('id')

        @property
        def name(self):
            return self._c('name')

        @property
        def private(self):
            return self._c('private')

        @property
        def uid(self):
            return self._c('uid')

        @property
        def avatar_url(self):
            return self._c('avatarUrl')

        @property
        def description(self):
            return self._c('desc')

        @property
        def pronouns(self):
            return self._c('pronouns')

        @property
        def color(self):
            return self._c('color')

        @property
        def pkid(self):
            return self._c('pkId')

    def __init__(self, token, userid):
        self.token = token
        self.userid = userid
        self._members = None

    def call(self, path, method='GET', data=None):
        if not path.startswith('/'):
            path = f'/{path}'
        url = f'https://v2.apparyllis.com/v1{path}'
        return curl(url, method=method, data=data, headers={
            'Content-Type': 'application/json',
            'Authorization': self.token,
        })

    def members(self):
        if self._members is None:
            data = json.loads(self.call(f'/members/{self.userid}'))
            self._members = [self.Member(m) for m in data]
        return self._members

    def fronters(self):
        data = json.loads(self.call('/fronters'))
        return [m.get('content', {}).get('member') for m in data]


class PluralKitApi:
    class Member:
        def __init__(self, data):
            self._data = data

        def __repr__(self):
            return json.dumps({
                key: getattr(self, key) for key in dir(self) if not key.startswith('_')
            }, indent=2)

        @property
        def id(self):
            return self._data.get('id')

        @property
        def uuid(self):
            return self._data.get('uuid')

        @property
        def name(self):
            return self._data.get('name')

        @property
        def display_name(self):
            return self._data.get('display_name')

        @property
        def pronouns(self):
            return self._data.get('pronouns')

        @property
        def avatar_url(self):
            return self._data.get('avatar_url')

        @property
        def description(self):
            return self._data.get('description')

        @property
        def color(self):
            return self._data.get('color')

    def __init__(self, token):
        self.token = token
        self._members = None

    def call(self, path, method='GET', data=None):
        # respect 2 qps ratelimit
        time.sleep(0.55)
        if not path.startswith('/'):
            path = f'/{path}'
        url = f'https://api.pluralkit.me/v2{path}'
        return curl(url, method=method, data=data, headers={
            'Content-Type': 'application/json',
            'Authorization': self.token,
        })

    def members(self):
        if self._members is None:
            data = json.loads(self.call('/systems/@me/members'))
            self._members = [self.Member(m) for m in data]
        return self._members

    def fronters(self):
        data = json.loads(self.call('/systems/@me/fronters'))
        return [m.get('id') for m in data.get('members', [])]

    def switch(self, members):
        return self.call('/systems/@me/switches', method='POST', data=json.dumps({
            'members': members,
        }))

    def update_member(self, member):
        return self.call(f'/members/{member["id"]}', method='PATCH', data=json.dumps(member))

# user data /user/{creds.sp_userid}

sp = SimplyPluralApi(creds.sp_token, creds.sp_userid)
pk = PluralKitApi(creds.pk_token)

sp_by_name = {}
sp_by_pkid = {}
sp_by_id = {}
print('SP Members')
for m in sp.members():
    if m.private:
        # don't sync private members to pk.
        continue
    sp_by_name[m.name] = m
    sp_by_pkid[m.pkid] = m
    sp_by_id[m.id] = m
    print(m)

pk_by_name = {}
pk_by_id = {}
print('PK Members')
for m in pk.members():
    pk_by_id[m.id] = m
    pk_by_name[m.name] = m
    print(m)

# sp id to pk id for each member
sp_to_pk = {}
for m in sp.members():
    if m.pkid in pk_by_id:
        sp_to_pk[m.id] = m.pkid
        continue
    if m.name in pk_by_name:
        sp_to_pk[m.id] = pk_by_name[m.name].id
        continue
    print(f'(No correspondence for sp member {m.name})')

def sync_member(spm, pkm):
    print(f'sync {spm.name} -> {pkm.name}')
    properties = [
        #('name', spm.name, pkm.name),
        ('display_name', spm.name if pkm.display_name == pkm.name else pkm.display_name, pkm.display_name),
        ('pronouns', spm.pronouns, pkm.pronouns),
        ('color', spm.color.lstrip('#') if spm.color else None, pkm.color),
        ('avatar_url', spm.avatar_url, pkm.avatar_url),
        ('description', spm.description, pkm.description),
    ]
    update = {
        'id': pkm.id,
        'uuid': pkm.uuid,
        #'name': spm.name,
        'color': pkm.color,
        'keep_proxy': False,
    }
    any_changes = False
    for (name, sp_value, pk_value) in properties:
        if not sp_value:
            continue # nothing to sync
        if sp_value != pk_value:
            update[name] = sp_value
            any_changes = True
    if not any_changes:
        return
    print('update:', json.dumps(update, indent=2))
    pk.update_member(update)

for spid, pkid in sp_to_pk.items():
    spm = sp_by_id[spid]
    pkm = pk_by_id[pkid]
    sync_member(spm, pkm)

pk_fronters = tuple(pk.fronters())
sp_fronters = tuple(sp_to_pk.get(s) for s in sp.fronters() if s in sp_to_pk)
print('sp fronters:', *sp_fronters)
print('pk fronters:', *pk_fronters)

if pk_fronters != sp_fronters:
    print('Updating pluralkit front.')
    pk.switch(sp_fronters)
