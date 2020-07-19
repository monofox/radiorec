#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

"""
radiorec.py – Recording internet radio streams
Copyright (C) 2013  Martin Brodbeck <martin@brodbeck-online.de>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import argparse
import configparser
import datetime
import os
import stat
import sys
import threading
import urllib.request


def check_duration(value):
    try:
        value = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            'Duration must be a positive integer.')

    if value < 1:
        raise argparse.ArgumentTypeError(
            'Duration must be a positive integer.')
    else:
        return value


def read_settings(args):
    settings_base_dir = ''
    if args.settings:
        settings_base_dir = args.settings
    elif sys.platform.startswith('linux'):
        settings_base_dir = os.getenv(
            'HOME') + os.sep + '.config' + os.sep + 'radiorec'
    elif sys.platform == 'win32':
        settings_base_dir = os.getenv('LOCALAPPDATA') + os.sep + 'radiorec'
    elif sys.platform == 'darwin':
        settings_base_dir = os.getenv('HOME') + os.sep + 'Library' + os.sep + 'Application Support' + os.sep + 'radiorec'
    settings_base_dir += os.sep
    config = configparser.ConfigParser()
    try:
        config.read_file(open(settings_base_dir + 'settings.ini'))
    except FileNotFoundError as err:
        print(str(err))
        print('Please copy/create the settings file to/in the appropriate '
              'location.')
        sys.exit()
    return dict(config.items())


def parse_icy(metadata):
    dta = {}

    for var in metadata.split(b';'):
        if var:
            varName, varValue = var.decode('utf-8').split('=', 1)
            # remove the surrounding quote chars: '
            varValue = varValue.strip()[1:-1]
            dta[varName] = varValue

    return dta


def record_worker(stoprec, streamurl, target_dir, args):
    headers = {
        'User-Agent': 'RadioRec'
    }
    if args.icy:
        headers['Icy-MetaData'] = '1'
    req = urllib.request.Request(
        streamurl,
        headers = headers
    )

    conn = urllib.request.urlopen(req)
    cur_dt_string = datetime.datetime.now().strftime('%Y-%m-%dT%H_%M_%S')
    filename = target_dir + os.sep + cur_dt_string + "_" + args.station
    if args.name:
        filename += '_' + args.name
    metaFilename = filename + '.meta'
    content_type = conn.getheader('Content-Type')
    if(content_type == 'audio/mpeg'):
        filename += '.mp3'
    elif(content_type == 'application/aacp' or content_type == 'audio/aacp' or content_type == 'audio/aac'):
        filename += '.aac'
    elif(content_type == 'application/ogg' or content_type == 'audio/ogg'):
        filename += '.ogg'
    elif(content_type == 'audio/x-mpegurl'):
        print('Sorry, M3U playlists are currently not supported')
        sys.exit()
    else:
        print('Unknown content type "' + content_type + '". Assuming mp3.')
        filename += '.mp3'

    # check for ICY stream details.
    icy_header_keys = [ keyName for keyName, keyValue in conn.getheaders() if keyName.lower().startswith('icy-') ]

    # check if there is any metaint header for AAC streams.
    if args.icy:
        icyMetaint = int(conn.getheader('icy-metaint')) if 'icy-metaint' in icy_header_keys else None
    else:
        icyMetaint = 0
    readLength = 1024
    if icyMetaint:
        readLength = icyMetaint

    if args.icy and icyMetaint:
        metaTarget = open(metaFilename, 'wb')
        startDateTime = datetime.datetime.now()
        if icy_header_keys:
            for keyName in icy_header_keys:
                metaLine = '{0}: {1}'.format(keyName, conn.getheader(keyName))
                verboseprint(metaLine)
                metaTarget.write((metaLine + os.linesep).encode('utf-8'))
    else:
        metaTarget = None

    with open(filename, "wb") as target:
        if args.public:
            verboseprint('Apply public write permissions (Linux only)')
            os.chmod(filename, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP |
                     stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
        verboseprint('Recording ' + args.station + '...')
        while(not stoprec.is_set() and not conn.closed):
            target.write(conn.read(readLength))
            if args.icy and icyMetaint:
                metalength = ord(conn.read(1)) * 16
                if metalength > 0:
                    metadata = conn.read(metalength).replace(b'\x00', b'')
                    if metadata:
                        timeOffset = datetime.datetime.now() - startDateTime
                        for keyName, keyValue in parse_icy(metadata).items():
                            metaLine = '[{0:0.2f}] {1}: {2}'.format(
                                            round(timeOffset.total_seconds()/60, 2),
                                            keyName, keyValue
                                        )
                            verboseprint(metaLine)
                            if metaTarget:
                                metaTarget.write((metaLine + os.linesep).encode('utf-8'))
                                metaTarget.flush()

    if args.icy and metaTarget:
        metaTarget.flush()
        metaTarget.close()

def record(args):
    settings = read_settings(args)
    streamurl = ''
    global verboseprint
    verboseprint = print if args.verbose else lambda *a, **k: None

    try:
        streamurl = settings['STATIONS'][args.station]
    except KeyError:
        print('Unkown station name: ' + args.station)
        sys.exit()
    if streamurl.endswith('.m3u'):
        verboseprint('Seems to be an M3U playlist. Trying to parse...')
        with urllib.request.urlopen(streamurl) as remotefile:
            for line in remotefile:
                if not line.decode('utf-8').startswith('#') and len(line) > 1:
                    tmpstr = line.decode('utf-8')
                    break
        streamurl = tmpstr
    verboseprint('stream url: ' + streamurl)
    target_dir = os.path.expandvars(settings['GLOBAL']['target_dir'])
    stoprec = threading.Event()

    recthread = threading.Thread(target=record_worker,
                                 args=(stoprec, streamurl, target_dir, args))
    recthread.setDaemon(True)
    recthread.start()
    recthread.join(args.duration * 60)

    if(recthread.is_alive):
        stoprec.set()


def list(args):
    settings = read_settings(args)
    for key in sorted(settings['STATIONS']):
        print(key)


def main():
    parser = argparse.ArgumentParser(description='This program records '
                                     'internet radio streams. It is free '
                                     'software and comes with ABSOLUTELY NO '
                                     'WARRANTY.')
    subparsers = parser.add_subparsers(help='sub-command help')
    parser_record = subparsers.add_parser('record', help='Record a station')
    parser_record.add_argument('station', type=str,
                               help='Name of the radio station '
                               '(see `radiorec.py list`)')
    parser_record.add_argument('duration', type=check_duration,
                               help='Recording time in minutes')
    parser_record.add_argument('name', nargs='?', type=str,
                               help='A name for the recording')
    parser_record.add_argument(
        '-p', '--public', action='store_true',
        help="Public write permissions (Linux only)")
    parser_record.add_argument(
        '-v', '--verbose', action='store_true', help="Verbose output")
    parser_record.add_argument(
        '-s', '--settings', nargs='?', type=str,
        help="specify alternative location for settings.ini")
    parser_record.add_argument(
        '--icy', action='store_true',
        help="Parse ICY headers and populates in meta file."
    )
    parser_record.set_defaults(func=record)
    parser_list = subparsers.add_parser('list', help='List all known stations')
    parser_list.set_defaults(func=list)
    parser_list.add_argument(
        '-s', '--settings', nargs='?', type=str,
        help="specify alternative location for settings.ini")

    if not len(sys.argv) > 1:
        print('Error: No argument specified.\n')
        parser.print_help()
        sys.exit(1)
    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()
