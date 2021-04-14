import argparse
import base64
import logging
import os
import re
import requests

FILE_CACHE = os.path.expanduser('~/.ru-pronounce/cache/')
PLAY_SOUND_CMD = 'afplay "{file}"'
SOUND_FILE_EXT = '.mp3'
FILEPATH_TEMPLATE = os.path.join(FILE_CACHE, '{word}' + '{suffix}' + SOUND_FILE_EXT)
FORVO_URL = 'https://forvo.com/word/{ru_word}/#ru'
AUDIO_URL = 'https://audio00.forvo.com/audios/mp3/{path}'
FIND_ENCODED_AUDIO_ARGS_RE = 'Play\((\d+,[^)]*)'
FALLBACK_AUDIO_URL = 'https://audio00.forvo.com/mp3/{path}'
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36'


def _setup_logging(log_level):
    logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=log_level)

def _setup_file_cache():
    if not os.path.isdir(FILE_CACHE):
        os.makedirs(FILE_CACHE, exist_ok=True)

def download_if_not_available(ru_word):
    filepath = _get_word_filepath(ru_word)
    if not os.path.isfile(filepath):
        logging.info(f'File {soundfile} not cached. Downloading...')
        download(ru_word)

def _get_word_filepath(ru_word, index=0):
    suffix = '' if index == 0 else f'_{index}'
    return FILEPATH_TEMPLATE.format(word=ru_word, suffix=suffix)

def pronounce(ru_word, pronunciation_index=0):
    filepath = _get_word_filepath(ru_word, pronunciation_index)
    if os.path.isfile(filepath):
        play(filepath)
    else:
        logging.error(f'File {filepath} does not exist')

def play(filepath):
    if os.path.isfile(filepath):
        logging.debug(f'Playing file {filepath}')
        os.system(PLAY_SOUND_CMD.format(file=filepath))
    else:
        logging.error(f'Could not find {filepath}')

def download(ru_word):
    audio_urls = []
    headers = {
        'User-Agent': USER_AGENT
    }
    r = requests.get(FORVO_URL.format(ru_word=ru_word), headers=headers)
    if r.status_code == 200:
        matches = re.findall(FIND_ENCODED_AUDIO_ARGS_RE, r.text)
        for match in matches:
            # each match is an arguments list to a function.
            args_list = [arg.strip('\'') for arg in match.split(',')]
            if args_list and args_list[4] != '':
                converted_match = base64.b64decode(args_list[4]).decode('utf-8')
                audio_url = AUDIO_URL.format(path=converted_match)
                audio_urls.append(audio_url)
            elif args_list and args_list[1] != '':
                converted_match = base64.b64decode(args_list[1]).decode('utf-8')
                audio_url = FALLBACK_AUDIO_URL.format(path=converted_match)
                audio_urls.append(audio_url)

    for i, url in enumerate(audio_urls):
        logging.debug(f'Downloading {url}...')
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            filepath = _get_word_filepath(ru_word, i)
            logging.debug(f'Download successful. Saving to {filepath}')
            with open(filepath, 'wb') as fd:
                fd.write(r.content)
        else:
            logging.error(f'Problem downloading {url}!'
                          f' Status code: {r.status_code}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ru_word')
    parser.add_argument(
            '-d',
            '--debug',
            action='store_true',
            help='print debug messages'
            )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
            '-c',
            '--cycle',
            nargs='?',
            type=int,
            const=-1,
            default=None,
            help='cycle through available word pronounciations.' \
            ' If an integer n is specified, cycle through the first' \
            ' n available pronounciations'
            )
    group.add_argument(
            '-n',
            '--play-n',
            type=int,
            help='play the nth available word pronounciation' \
            ' where n is the integer provided to this option'
            )
    group.add_argument(
            '-r',
            '--random',
            action='store_true',
            help='play a random available word pronounciation'
            )

    args = parser.parse_args()
    log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    _setup_logging(log_level)
    _setup_file_cache()
    download_if_not_available(args.ru_word)
    pronounce(args.ru_word, pronunciation_index=args.play_n)
