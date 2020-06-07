#!/usr/bin/env python3

import os
import signal
import subprocess
import sys
import time

PIDFILE = '/var/run/vpnclient.pid'
VPNIF = 'tun0'
DEFAULT_REMOTE_HOST = '4.2.2.2'
VPN_ROUTE_PREFIX = '10.'
PROCESS_SEARCH_STRING = 'vpnprov'


def check_connection(remote_host=DEFAULT_REMOTE_HOST) -> bool:
    """ Check internet connection by pinging remote_host """
    ping_proc = subprocess.run(['ping', '-c', '5', remote_host])
    return ping_proc.returncode == 0


def get_route(remote_host=DEFAULT_REMOTE_HOST):
    """ Get first route used in tracerouting to remote_host"""
    out = subprocess.check_output(
        ['traceroute', '-m', '1', remote_host],
        stderr=subprocess.DEVNULL
    ).decode()
    first_route = out.split()[1]
    return first_route


def run_vpn():
    delete_routes()
    subprocess.run(['sh', '/etc/netstart', VPNIF])


def kill_vpn_client(vpnclient_pid):
    try:
        os.kill(vpnclient_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(5)
    delete_routes()


def delete_routes():
    out = subprocess.check_output(["netstat", "-rn"]).decode()
    out = out.splitlines()
    for line in out:
        if VPNIF in line:
            route = line.split()[0]
            subprocess.run(['route', 'delete', route])


def get_pid_by_str(search_str):
    """ Search pid by its command line """
    out = subprocess.check_output(['ps', '-A', '-o pid,command'])
    out = out.decode()

    for line in out.splitlines():
        pid, cmd = line.strip().split(' ', 1)
        if search_str in cmd.lower():
            return int(pid)

    return None


def write_pid(pid, pidfile):
    with open(pidfile, 'w') as pfile:
        pfile.write('%s\n' % str(pid))


def main():
    vpn_client_pid = get_pid_by_str(PROCESS_SEARCH_STRING)

    if vpn_client_pid is not None:
        if not (check_connection() and get_route().startswith(VPN_ROUTE_PREFIX)):
            kill_vpn_client(vpn_client_pid)
            run_vpn()

    else:
        run_vpn()

    return 0


if __name__ == '__main__':
    sys.exit(main())
