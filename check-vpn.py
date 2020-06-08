#!/usr/bin/env python3

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time

DEFAULT_REMOTE_HOST = '4.2.2.2'
TAG_IN_PATTERN = re.compile(r"^<(.*)>$")
TAG_OUT_PATTERN = re.compile(r"^</(.*)>$")


def check_connection(remote_host=DEFAULT_REMOTE_HOST) -> bool:
    """ Check internet connection by pinging remote_host """
    ping_proc = subprocess.run(['ping', '-c', '5', remote_host], capture_output=True)
    if ping_proc.stdout:
        logging.info("ping stdout:\n%s", ping_proc.stdout.decode().strip())
    if ping_proc.stderr:
        logging.info("ping stderr:\n%s", ping_proc.stderr.decode().strip())
    return ping_proc.returncode == 0


def get_route(remote_host=DEFAULT_REMOTE_HOST):
    """ Get first route used in tracerouting to remote_host"""
    out = subprocess.check_output(
        ['traceroute', '-m', '1', remote_host],
        stderr=subprocess.DEVNULL
    ).decode()
    first_route = out.split()[1]
    return first_route


def run_vpn(iface) -> bool:
    logging.info("Removing %s routes before starting VPN client", iface)
    if not delete_iface_routes(iface):
        return False

    logging.warning("Bringing up VPN interface %s", iface)
    res = subprocess.run(['sh', '/etc/netstart', iface])
    if res.returncode:
        logging.error("Failed to bring up VPN interface %s", iface)
        return False

    return True


def kill_vpn_client(vpnclient_pid):
    try:
        logging.warning("Killing VPN client process %s", vpnclient_pid)
        os.kill(vpnclient_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(5)
    logging.warning("VPN client process %s is killed", vpnclient_pid)


def delete_iface_routes(iface) -> bool:
    logging.info("Fetching route table")
    out = subprocess.check_output(["netstat", "-rn", "-finet"]).decode()
    out = out.splitlines()

    for line in out:
        if iface in line:
            logging.debug("Processing route line: %s", line)
            route = line.split()[0]

            logging.warning("Removing route %s for iface %s", route, iface)
            res = subprocess.run(['route', 'delete', route])
            if res.returncode:
                logging.error("Failed to remove route %s for iface %s", route, iface)

    return True


def get_pid_by_str(search_str):
    """ Search pid by its command line """
    logging.info("Searching process by string: %s", search_str)
    out = subprocess.check_output(['ps', '-A', '-o pid,command'])
    out = out.decode()

    for line in out.splitlines():
        if "openvpn" not in line:
            continue
        pid, cmd = line.strip().split(' ', 1)
        if search_str in cmd.lower():
            logging.info("Process found: %s", line)
            return int(pid)

    logging.info("No processes are found: %s", search_str)
    return None


def parse_ovpn_config(path):
    logging.info("Reading ovpn config file: %s", path)
    with open(path) as fp:
        content = str(fp.read())

    config = dict()
    in_tag = None
    for line in content.splitlines():
        logging.debug("Reading line: %s", line)
        line = line.strip()

        # skip comments and empty lines
        if line.startswith("#") or not line:
            continue

        # handle exit tag lines (</some_tag>)
        line_re = TAG_OUT_PATTERN.match(line)
        if line_re:
            in_tag = None
            continue

        # handle enter tag lines (<some_tag>)
        line_re = TAG_IN_PATTERN.match(line)
        if line_re:
            in_tag = line_re.group(1)
            config[in_tag] = str()
            continue

        # handle content inside tags
        if in_tag is not None:
            config[in_tag] += line
            continue

        # handle common lines
        if " " not in line:
            key, value = line, True
        else:
            key, value = line.split(" ", 1)
        config[key] = value

    logging.info("Config parsed successfully: %s", path)
    return config


def run_vpn_checks(remote_host=DEFAULT_REMOTE_HOST,
                   route_prefix='') -> bool:
    """ Run some tests to check VPN connection """

    logging.info("Checking internet connection")
    if not check_connection(remote_host=remote_host):
        logging.warning("Remote host %s is not available through ICMP", remote_host)
        return False

    if route_prefix:
        logging.info("Checking default route")
        if not get_route().startswith(route_prefix):
            logging.warning("Route table has no expected default route %s", route_prefix)
            return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Check VPN routes.")
    parser.add_argument("ovpn_file",
                        metavar="OVPN_FILE",
                        help="path to OpenVPN client config")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print verbose output")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="print debug output")
    parser.add_argument("-p", "--route-prefix",
                        help="VPN route prefix (which should exists if connection is fine)")
    parser.add_argument("-r", "--remote-host",
                        help="remote host for checking connection",
                        default=DEFAULT_REMOTE_HOST)
    args = parser.parse_args()

    loglevel = logging.WARNING
    if args.verbose:
        loglevel = logging.INFO
    if args.debug:
        loglevel = logging.DEBUG
    logging.basicConfig(
        # format="%(levelname)-5s %(asctime)s %(message)s",
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=loglevel
    )

    logging.info("Starting with args: %s", args)

    config_name = os.path.basename(args.ovpn_file)
    try:
        config = parse_ovpn_config(args.ovpn_file)
    except FileNotFoundError as err:
        logging.error("%s: %s", err.strerror, args.ovpn_file)
        return err.errno

    vpn_good = False
    while not vpn_good:
        vpn_client_pid = get_pid_by_str(config_name)
        if not vpn_client_pid:
            logging.warning("VPN client %s is not running", config_name)
            if not run_vpn(config['dev']):
                logging.error("Failed to start VPN client %s", config_name)
                return 1

            # TODO add real log checks instead of sleep
            logging.info("Wait some time before client starts")
            time.sleep(30)

        logging.warning("VPN client %s is running", config_name)

        if run_vpn_checks(remote_host=args.remote_host,
                          route_prefix=args.route_prefix):
            vpn_good = True
            logging.warning("VPN connection %s is ok", config_name)
        else:
            logging.warning("VPN connection %s is unstable, need to restart", config_name)
            kill_vpn_client(vpn_client_pid)
            delete_iface_routes(config["dev"])

    return 0


if __name__ == '__main__':
    sys.exit(main())
