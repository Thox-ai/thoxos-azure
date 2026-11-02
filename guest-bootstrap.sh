#!/usr/bin/env bash
# THOX.ai: install the published ThoxInux engineering-preview kernel on an
# Ubuntu 24.04 x86-64 host. This is an Ubuntu host plus a real QEMU guest.
# This file prepares a deployment; its existence does not prove Azure execution.
set -Eeuo pipefail
umask 077

die() { printf 'THOX bootstrap refused: %s\n' "$*" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || die 'Run this bootstrap as root.'
[[ $(uname -m) == x86_64 ]] || die 'An x86-64 host is required.'
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || die 'Ubuntu 24.04 is required.'
[[ -d /run/systemd/system ]] || die 'A running systemd host is required.'

readonly RELEASE=v0.9.1
readonly SOURCE_COMMIT=84b4a7e740496297ca79dff735834ca2a8f2bad3
readonly INSTALL_ROOT=/opt/thoxinux
readonly PAYLOAD=/opt/thoxinux/releases/v0.9.1
readonly OWNER_FILE=/etc/thoxinux-release-host.json
readonly SERVICE_USER=thoxinux
for path in "$INSTALL_ROOT" "$OWNER_FILE" /usr/local/lib/thoxinux /var/lib/thoxinux /var/log/thoxinux /run/thoxinux-bootstrap; do
    [[ ! -L $path ]] || die "Refusing symlink: $path"
done
install -d -o root -g root -m 0700 /run/thoxinux-bootstrap
exec 9>/run/thoxinux-bootstrap/install.lock
flock -n 9 || die 'Another bootstrap is running.'
if [[ -f $OWNER_FILE ]]; then
    python3 - "$OWNER_FILE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
if d.get('schema') != 'thoxinux.azure.release-host.v1' or d.get('release') != 'v0.9.1':
    raise SystemExit('Existing installation ownership or release differs; manual review required.')
PY
else
    for path in "$INSTALL_ROOT" /usr/local/lib/thoxinux /var/lib/thoxinux /var/log/thoxinux /etc/systemd/system/thoxinux.service /usr/local/sbin/thoxos-release-status /usr/local/sbin/thoxinux-console; do
        [[ ! -e $path && ! -L $path ]] || die "Unowned installation path already exists: $path"
    done
    if getent passwd "$SERVICE_USER" >/dev/null; then
        die 'The thoxinux account already exists without this installation ownership record.'
    fi
    printf '{"schema":"thoxinux.azure.release-host.v1","release":"v0.9.1"}\n' >"$OWNER_FILE"
    chmod 0600 "$OWNER_FILE"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends \
    ca-certificates curl python3 qemu-system-x86 ovmf util-linux
if ! getent passwd "$SERVICE_USER" >/dev/null; then
    useradd --system --user-group --home-dir /var/lib/thoxinux --no-create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
[[ $(getent passwd "$SERVICE_USER" | cut -d: -f6) == /var/lib/thoxinux ]] || die 'Service account home differs.'
[[ $(getent passwd "$SERVICE_USER" | cut -d: -f7) == /usr/sbin/nologin ]] || die 'Service account shell differs.'
install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_ROOT/releases" /usr/local/lib/thoxinux
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 /var/lib/thoxinux /var/log/thoxinux
work=$(mktemp -d /var/tmp/thoxinux-bootstrap.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
readonly ASSET_BASE=https://github.com/Thox-ai/thoxinux/releases/download/v0.9.1
readonly SOURCE_BASE=https://raw.githubusercontent.com/Thox-ai/thoxinux/84b4a7e740496297ca79dff735834ca2a8f2bad3
fetch() {
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --retry 3 --retry-delay 2 --connect-timeout 20 --max-time 180 \
        --output "$2" "$1"
}
for name in GINUX.EFI build-receipt.json SHA256SUMS; do
    fetch "$ASSET_BASE/$name" "$work/$name"
done
for name in GINUX_LICENSE GINUX_NOTICE; do
    fetch "$SOURCE_BASE/kernel/ginux/artifacts/$name" "$work/$name"
done
fetch "$SOURCE_BASE/kernel/ginux/scripts/run-vm.sh" "$work/run-vm.sh"

# This module is also used by the read-only status command. Hash pins came from
# GitHub's released asset digests and exact files at the v0.9.1 source commit.
cat >"$work/release.py" <<'PY'
import hashlib,json,pathlib,re
RELEASE='v0.9.1'
SOURCE_COMMIT='84b4a7e740496297ca79dff735834ca2a8f2bad3'
ROOT=pathlib.Path('/opt/thoxinux/releases/v0.9.1')
EXPECTED={
 'GINUX.EFI':'1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89',
 'build-receipt.json':'5b6f2eb2aff311db9924d7d3f6da4d2802b6f04b01d72d73a3f5784340df384c',
 'SHA256SUMS':'85dce449380f5d6f23e1ea030bcff7b67c4305145a1ad39b6d99b4d9c8307d70',
 'GINUX_LICENSE':'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
 'GINUX_NOTICE':'65efae7eeef0e462670e5e80a2a122f2f5910559f2c73e9e88c53d45414f1bcf',
 'run-vm.sh':'c7694116061f7ea5b3d9f4eda416d9eca1410d7568d6560edf2e4617708e91c8',
}
def verify(root=ROOT):
    root=pathlib.Path(root)
    actual={}
    for name,want in EXPECTED.items():
        p=root/name
        if p.is_symlink() or not p.is_file():
            raise ValueError('Missing regular payload file: '+name)
        actual[name]=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual[name]!=want:
            raise ValueError('Pinned digest differs: '+name)
    entries={}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        m=re.fullmatch(r'([0-9a-f]{64})  (GINUX\.EFI|build-receipt\.json|GINUX_LICENSE|GINUX_NOTICE)',line)
        if not m or m[2] in entries:
            raise ValueError('Unsafe or duplicate release checksum entry')
        entries[m[2]]=m[1]
    wanted={name:EXPECTED[name] for name in ('GINUX.EFI','build-receipt.json','GINUX_LICENSE','GINUX_NOTICE')}
    if entries!=wanted:
        raise ValueError('Release checksum inventory differs from the pinned payload')
    receipt=json.loads((root/'build-receipt.json').read_text())
    if receipt.get('efi_sha256')!=EXPECTED['GINUX.EFI'] or receipt.get('efi_size_bytes')!=12507648:
        raise ValueError('Build receipt does not bind the expected EFI')
    if receipt.get('upstream_commit')!='36503bda2d87465765e1b94e65f9d100f27ed5be':
        raise ValueError('Unexpected upstream source in build receipt')
    return actual
PY
python3 - "$work" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
import release
release.verify(sys.argv[1])
print('THOX published release payload and launcher hashes verified.')
PY
if [[ -e $PAYLOAD ]]; then
    [[ -d $PAYLOAD && ! -L $PAYLOAD ]] || die 'Existing release path is not a regular directory.'
    python3 - "$work" "$PAYLOAD" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
import release
release.verify(sys.argv[2])
PY
else
    staged=$(mktemp -d "$INSTALL_ROOT/releases/.v0.9.1.XXXXXXXX")
    chmod 0755 "$staged"
    for name in GINUX.EFI build-receipt.json SHA256SUMS GINUX_LICENSE GINUX_NOTICE; do
        install -o root -g root -m 0644 "$work/$name" "$staged/$name"
    done
    install -o root -g root -m 0755 "$work/run-vm.sh" "$staged/run-vm.sh"
    mv -T "$staged" "$PAYLOAD"
fi
install -o root -g root -m 0644 "$work/release.py" /usr/local/lib/thoxinux/release.py
python3 - "$PAYLOAD" <<'PY'
import datetime,json,pathlib,sys
sys.path.insert(0,'/usr/local/lib/thoxinux')
import release
p=pathlib.Path(sys.argv[1])/'deployment-provenance.json'
if not p.exists():
    p.write_text(json.dumps({'schema':'thoxinux.release.deployment-provenance.v1',
      'installed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'release':release.RELEASE,'source_commit':release.SOURCE_COMMIT,
      'artifacts':release.verify(),'runtime':'Ubuntu host with real ThoxInux QEMU TCG guest',
      'claim_limits':['Engineering preview','No native Hyper-V kernel qualification',
                      'No graphical desktop or physical hardware acceptance',
                      'Azure execution must be verified on the deployed host']},indent=2)+'\n')
    p.chmod(0o644)
PY

# Preserve the pinned launcher. This wrapper adds only a local QMP socket;
# no TCP serial console, VNC server, host device, or host share is exposed.
cat >/usr/local/lib/thoxinux/qemu-local <<'SH'
#!/bin/sh
exec /usr/bin/qemu-system-x86_64 "$@" -qmp unix:/run/thoxinux/qmp.sock,server=on,wait=off
SH
chmod 0755 /usr/local/lib/thoxinux/qemu-local

cat >/usr/local/lib/thoxinux/supervisor.py <<'PY'
#!/usr/bin/env python3
import datetime,fcntl,json,os,pathlib,signal,socket,stat,subprocess,sys,threading,time,uuid
from release import ROOT,verify
RUN=pathlib.Path('/run/thoxinux')
LOG=pathlib.Path('/var/log/thoxinux')
STATE=pathlib.Path('/var/lib/thoxinux')
PROMPT=b'ginux> '
stop=threading.Event()
condition=threading.Condition()
commands=threading.Lock()
buffer=bytearray()
base=0
total=0
panic=False
boot_id=str(uuid.uuid4())
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
log_path=LOG/('boot-'+boot_id+'.serial.log')
verify()
lock=open(RUN/'supervisor.lock','w')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
for name in ('console.sock','qmp.sock'):
    p=RUN/name
    if p.exists() or p.is_symlink():
        if not stat.S_ISSOCK(p.lstat().st_mode):
            raise RuntimeError('Refusing unexpected runtime path: '+str(p))
        p.unlink()
env=os.environ.copy()
env.update(HOME=str(STATE),GINUX_ARTIFACT_DIR=str(ROOT),GINUX_STATE_DIR=str(STATE/'guest'),
           QEMU_ACCEL='tcg',GINUX_MEMORY='2G',QEMU_SYSTEM_X86_64='/usr/local/lib/thoxinux/qemu-local')
proc=subprocess.Popen([str(ROOT/'run-vm.sh')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT,env=env,bufsize=0)
record={'schema':'thoxinux.guest.boot.v1','boot_id':boot_id,'started_utc':started,
        'supervisor_pid':os.getpid(),'qemu_pid':proc.pid,'serial_log':str(log_path),
        'boot_markers':{},'outcome':'starting'}
def save_record():
    temp=LOG/('current-'+boot_id+'.tmp')
    temp.write_text(json.dumps(record,indent=2)+'\n')
    temp.replace(LOG/'current.json')
save_record()
def reader():
    global base,total,panic
    with log_path.open('xb',buffering=0) as out:
        while True:
            data=proc.stdout.read(65536)
            if not data:
                break
            out.write(data)
            with condition:
                buffer.extend(data)
                total+=len(data)
                if len(buffer)>1048576:
                    drop=len(buffer)-1048576
                    del buffer[:drop]
                    base+=drop
                panic=panic or b'GINUX_KERNEL_PANIC' in buffer or b'GINUX_BOOT_PANIC' in buffer
                condition.notify_all()
        with condition:
            condition.notify_all()
reader_thread=threading.Thread(target=reader,daemon=True)
reader_thread.start()
def wait_for(cursor,timeout=60,allow_shutdown=False):
    deadline=time.monotonic()+timeout
    with condition:
        while time.monotonic()<deadline:
            if cursor<base:
                raise RuntimeError('Console response exceeded bounded capture size; inspect serial log')
            data=bytes(buffer[cursor-base:])
            if panic:
                raise RuntimeError('Guest panic; inspect serial log')
            if PROMPT in data or (allow_shutdown and b'GINUX_KERNEL_OK' in data):
                return data
            if proc.poll() is not None:
                raise RuntimeError('Guest exited before response')
            condition.wait(min(0.25,max(0,deadline-time.monotonic())))
    raise TimeoutError('Guest console response timeout')
def command(text):
    if not isinstance(text,str) or not text or len(text)>240 or any(ord(c)<32 or ord(c)>126 for c in text):
        raise ValueError('Supply one printable ASCII guest command, at most 240 characters')
    with commands:
        with condition:
            cursor=total
        proc.stdin.write((text+'\n').encode())
        proc.stdin.flush()
        return wait_for(cursor,allow_shutdown=text.strip()=='shutdown').decode('utf-8','replace')
def smoke_once():
    checks=[('status',['controller: running (ring 3)','integrity: sealed']),
            ('components',['kernel: active, ring 0','controller: active, ring 3']),
            ('domain list',['NAME','STATE','PERSONALITY']),
            ('help',['Ginux controller shell','Available Commands:'])]
    evidence={'schema':'thoxinux.guest.smoke.v1','ok':False,'boot_id':boot_id,'commands':[],
              'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope':'Read-only controller commands on this boot; no domain lifecycle qualification'}
    try:
        for text,expected in checks:
            output=command(text)
            passed=all(marker in output for marker in expected)
            evidence['commands'].append({'command':text,'passed':passed,'output':output})
            if not passed:
                raise RuntimeError('Expected controller response missing: '+text)
        evidence['ok']=True
    except Exception as exc:
        evidence['error']=str(exc)
    attempt=uuid.uuid4().hex
    stem='smoke-'+boot_id
    payload=json.dumps(evidence,indent=2)+'\n'
    with (LOG/(stem+'-'+attempt+'.json')).open('x') as f:
        f.write(payload)
    temporary=LOG/(stem+'-'+attempt+'.tmp')
    temporary.write_text(payload)
    temporary.replace(LOG/(stem+'.json'))
    if not evidence['ok']:
        raise RuntimeError(evidence['error'])
def stop_requested(signum,frame):
    stop.set()
signal.signal(signal.SIGTERM,stop_requested)
signal.signal(signal.SIGINT,stop_requested)
server=None
error=None
try:
    startup=wait_for(0,300)
    markers={'unified_identity':b'ThoxInux unified (standalone UEFI)' in startup,
             'controller':b'Ginux controller v0 (ring 3)' in startup,
             'prompt':PROMPT in startup,'panic':panic}
    if not all(markers[k] for k in ('unified_identity','controller','prompt')) or panic:
        raise RuntimeError('Expected unified release kernel identity was not observed')
    record.update(boot_markers=markers,outcome='booted')
    save_record()
    # Run once on every actual boot, including host reboots and service recovery.
    # The separate status command only reads this receipt and never injects input.
    smoke_once()
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    server.bind(str(RUN/'console.sock'))
    os.chmod(RUN/'console.sock',0o600)
    server.listen(2)
    server.settimeout(0.5)
    while not stop.is_set() and proc.poll() is None:
        try:
            conn,_=server.accept()
        except socket.timeout:
            continue
        with conn:
            conn.settimeout(5)
            request=bytearray()
            try:
                while b'\n' not in request:
                    block=conn.recv(1024)
                    if not block:
                        break
                    request.extend(block)
                    if len(request)>2048:
                        raise ValueError('Console request too large')
                data=json.loads(request)
                response={'ok':True,'boot_id':boot_id,'output':command(data['command'])}
            except (ValueError,KeyError,RuntimeError,TimeoutError,OSError) as exc:
                response={'ok':False,'boot_id':boot_id,'error':str(exc)}
            try:
                conn.sendall((json.dumps(response)+'\n').encode())
            except OSError:
                pass
except Exception as exc:
    error=str(exc)
    print('ThoxInux supervisor: '+error,file=sys.stderr,flush=True)
finally:
    if server is not None:
        server.close()
    if proc.poll() is None:
        try:
            proc.stdin.write(b'shutdown\n')
            proc.stdin.flush()
            proc.wait(timeout=45)
        except (OSError,subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    reader_thread.join(timeout=5)
    record.update(outcome='stopped' if error is None and proc.returncode==0 else 'failed',
                  error=error,exit_code=proc.returncode,
                  stopped_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save_record()
    (RUN/'console.sock').unlink(missing_ok=True)
raise SystemExit(0 if error is None and proc.returncode==0 else 1)
PY

cat >/usr/local/sbin/thoxinux-console <<'PY'
#!/usr/bin/env python3
"""Send a local guest command, or run one bounded read-only controller smoke."""
import datetime,json,os,pathlib,pwd,socket,sys,uuid
if os.geteuid()!=0:
    raise SystemExit('Use sudo or Azure Run Command to administer the local guest console.')
# Authorization is root-only, but all socket and log operations run with the
# service account's authority. Never write as root into its writable log tree.
account=pwd.getpwnam('thoxinux')
if account.pw_uid==0 or account.pw_gid==0:
    raise SystemExit('Refusing a privileged guest service account.')
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
if os.geteuid()!=account.pw_uid or os.getegid()!=account.pw_gid:
    raise SystemExit('Could not drop to the guest service account.')
def send(command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(75)
        s.connect('/run/thoxinux/console.sock')
        s.sendall((json.dumps({'command':command})+'\n').encode())
        data=bytearray()
        while b'\n' not in data:
            block=s.recv(65536)
            if not block:
                break
            data.extend(block)
            if len(data)>2097152:
                raise RuntimeError('Console reply too large')
    result=json.loads(data)
    if not result.get('ok'):
        raise RuntimeError(result.get('error','Guest command failed'))
    return result
if sys.argv[1:]==['--smoke']:
    checks=[('status',['controller: running (ring 3)','integrity: sealed']),
            ('components',['kernel: active, ring 0','controller: active, ring 3']),
            ('domain list',['NAME','STATE','PERSONALITY']),
            ('help',['Ginux controller shell','Available Commands:'])]
    evidence={'schema':'thoxinux.guest.smoke.v1','ok':False,'commands':[],
              'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope':'Read-only controller commands on the running guest; no domain lifecycle qualification'}
    try:
        for cmd,expected in checks:
            result=send(cmd)
            if evidence.get('boot_id',result['boot_id'])!=result['boot_id']:
                raise RuntimeError('Guest restarted during smoke')
            evidence['boot_id']=result['boot_id']
            passed=all(marker in result['output'] for marker in expected)
            evidence['commands'].append({'command':cmd,'passed':passed,'output':result['output']})
            if not passed:
                raise RuntimeError('Expected controller response missing: '+cmd)
        evidence['ok']=True
    except Exception as exc:
        evidence['error']=str(exc)
    stem='smoke-'+evidence.get('boot_id','unavailable')
    attempt=uuid.uuid4().hex
    p=pathlib.Path('/var/log/thoxinux')/(stem+'-'+attempt+'.json')
    payload=json.dumps(evidence,indent=2)+'\n'
    with p.open('x') as f:
        f.write(payload)
    p.chmod(0o600)
    # Keep every attempt; only the per-boot current receipt is replaced.
    current=p.with_name(stem+'.json')
    temp=p.with_name(stem+'-'+attempt+'.tmp')
    with temp.open('x') as f:
        f.write(payload)
    temp.chmod(0o600)
    temp.replace(current)
    print(json.dumps(evidence))
    raise SystemExit(0 if evidence['ok'] else 1)
if not sys.argv[1:]:
    raise SystemExit('Usage: thoxinux-console status | components | "domain list" | help | --smoke')
result=send(' '.join(sys.argv[1:]))
print(result['output'],end='')
PY

cat >/usr/local/sbin/thoxos-release-status <<'PY'
#!/usr/bin/env python3
"""Read-only, bounded release verification. Never starts or changes a guest."""
import datetime,hashlib,json,os,pathlib,re,socket,subprocess,sys
sys.path.insert(0,'/usr/local/lib/thoxinux')
from release import RELEASE,SOURCE_COMMIT,EXPECTED,ROOT,verify
result={'marker':'THOXOS_RELEASE_STATUS_V1','ok':False,'release':RELEASE,
 'source_commit':SOURCE_COMMIT,'efi_sha256':None,'expected_efi_sha256':EXPECTED['GINUX.EFI'],
 'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'checksums_verified':False,'service_state':'unknown','qmp_status':'unknown',
 'serial_markers':{},'smoke':{'ok':False,'current_boot':False,'commands':[]},'errors':[],
 'claim_limits':['Ubuntu host with real ThoxInux QEMU TCG guest',
                 'Engineering preview, no native Hyper-V boot or desktop qualification']}
try:
    result['efi_sha256']=hashlib.sha256((ROOT/'GINUX.EFI').read_bytes()).hexdigest()
    verify()
    result['checksums_verified']=True
except Exception as exc:
    result['errors'].append('payload: '+str(exc))
try:
    r=subprocess.run(['systemctl','is-active','thoxinux.service'],capture_output=True,text=True,timeout=10)
    result['service_state']=r.stdout.strip()
    current=json.loads(pathlib.Path('/var/log/thoxinux/current.json').read_text())
    bid=current.get('boot_id','')
    if not re.fullmatch(r'[0-9a-f-]{36}',bid):
        raise ValueError('Invalid boot identity')
    result['boot_id']=bid
    p=pathlib.Path('/var/log/thoxinux')/('boot-'+bid+'.serial.log')
    if str(p)!=current.get('serial_log') or p.is_symlink():
        raise ValueError('Unexpected serial evidence path')
    result['serial_log']=str(p)
    with p.open('rb') as f:
        first=f.read(1048576)
        f.seek(max(0,p.stat().st_size-1048576))
        last=f.read(1048576)
    result['serial_markers']={
      'unified_identity':b'ThoxInux unified (standalone UEFI)' in first,
      'controller':b'Ginux controller v0 (ring 3)' in first,
      'prompt':b'ginux> ' in first,
      'panic':any(s in first or s in last for s in (b'GINUX_KERNEL_PANIC',b'GINUX_BOOT_PANIC'))}
    smoke=json.loads((pathlib.Path('/var/log/thoxinux')/('smoke-'+bid+'.json')).read_text())
    commands=smoke.get('commands',[])
    result['smoke']={'ok':smoke.get('ok') is True and len(commands)==4 and all(c.get('passed') is True for c in commands),
                     'current_boot':smoke.get('boot_id')==bid,
                     'commands':[c.get('command') for c in commands]}
except Exception as exc:
    result['errors'].append('boot evidence: '+str(exc))
try:
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect('/run/thoxinux/qmp.sock')
        f=s.makefile('rwb',buffering=0)
        if 'QMP' not in json.loads(f.readline(65536)):
            raise ValueError('Missing QMP greeting')
        for command in ('qmp_capabilities','query-status'):
            f.write((json.dumps({'execute':command,'id':command})+'\n').encode())
            for unused in range(32):
                msg=json.loads(f.readline(65536))
                if msg.get('id')==command:
                    if 'error' in msg:
                        raise ValueError('QMP request refused: '+command)
                    if command=='query-status':
                        result['qmp_status']=msg['return']['status']
                    break
            else:
                raise ValueError('QMP response limit exceeded')
except Exception as exc:
    result['errors'].append('QMP: '+str(exc))
m=result['serial_markers']
result['ok']=(result['checksums_verified'] and result['service_state']=='active' and
 result['qmp_status']=='running' and all(m.get(k) for k in ('unified_identity','controller','prompt')) and
 not m.get('panic',True) and result['smoke']['ok'] and result['smoke']['current_boot'] and not result['errors'])
print(json.dumps(result,indent=2))
raise SystemExit(0 if result['ok'] else 1)
PY
chmod 0755 /usr/local/lib/thoxinux/supervisor.py /usr/local/sbin/thoxinux-console /usr/local/sbin/thoxos-release-status

cat >/etc/systemd/system/thoxinux.service <<'UNIT'
[Unit]
Description=ThoxInux v0.9.1 real kernel guest under QEMU TCG
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User=thoxinux
Group=thoxinux
UMask=0077
WorkingDirectory=/var/lib/thoxinux
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /usr/local/lib/thoxinux/supervisor.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=90
KillMode=mixed
RuntimeDirectory=thoxinux
RuntimeDirectoryMode=0700
StateDirectory=thoxinux
StateDirectoryMode=0700
LogsDirectory=thoxinux
LogsDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/thoxinux /var/log/thoxinux /run/thoxinux
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
MemoryMax=4G
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 /etc/systemd/system/thoxinux.service
systemctl daemon-reload
systemctl enable --now thoxinux.service

# Wait only for this installation's actual controller, with a bounded deadline.
# The supervisor sends smoke commands once for each boot. Status never sends them.
python3 - <<'PY'
import pathlib,subprocess,time
deadline=time.monotonic()+330
while time.monotonic()<deadline:
    if pathlib.Path('/run/thoxinux/console.sock').exists():
        break
    if subprocess.run(['systemctl','is-failed','--quiet','thoxinux.service']).returncode==0:
        raise SystemExit('Guest service failed; inspect journalctl -u thoxinux and /var/log/thoxinux.')
    time.sleep(2)
else:
    raise SystemExit('Guest boot timed out; evidence preserved in /var/log/thoxinux.')
PY
if ! /usr/local/sbin/thoxos-release-status >"$work/status-before-smoke.json"; then
    /usr/local/sbin/thoxinux-console --smoke
fi
/usr/local/sbin/thoxos-release-status
printf 'THOX release bootstrap completed with verified current-boot evidence.\n'
