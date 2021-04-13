import argparse
import base64
import logging
import os
import re
import requests

FILE_CACHE = os.path.expanduser('~/.ru-pronounce/cache/')
PLAY_SOUND_CMD = 'afplay "{file}"'
SOUND_FILE_EXT = '.mp3'
FORVO_URL = 'https://forvo.com/word/{ru_word}/#ru'
AUDIO_URL = 'https://audio00.forvo.com/audios/mp3/{path}'
FIND_ENCODED_AUDIO_ARGS_RE = 'Play\((\d+,[^)]*)'
FALLBACK_AUDIO_URL = 'https://audio00.forvo.com/mp3/{path}'
# Play(id, path_mp3, path_ogg, true, path_audio_mp3, path_audio_ogg, quality);
# Play(3710636,'OTUxNDEzNi8xMzgvOTUxNDEzNl8xMzhfNzM2MTE3Lm1wMw==','OTUxNDEzNi8xMzgvOTUxNDEzNl8xMzhfNzM2MTE3Lm9nZw==',false,'Zy9qL2dqXzk1MTQxMzZfMTM4XzczNjExNy5tcDM=','Zy9qL2dqXzk1MTQxMzZfMTM4XzczNjExNy5vZ2c=','h');return false;
# https://audio00.forvo.com/audios/mp3/0/9/09_8983396_138_736117_1.mp3
# https://audio00.forvo.com/audios/mp3/g/j/gj_9514136_138_736117.mp3
# 9514136/138/9514136_138_736117.mp3
# 9514136/138/9514136_138_736117.ogg
# g/j/gj_9514136_138_736117.mp3
# g/j/gj_9514136_138_736117.ogg

# Intermediate
# https://audio00.forvo.com/mp3/9514136/138/9514136_138_736117.mp3

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36'

def _setup_logging(log_level):
    logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=log_level)

def _setup_file_cache():
    if not os.path.isdir(FILE_CACHE):
        os.makedirs(FILE_CACHE, exist_ok=True)

def pronounce(ru_word):
    soundfile = ru_word + SOUND_FILE_EXT
    filepath = os.path.join(FILE_CACHE, soundfile)
    if os.path.isfile(filepath):
        play(filepath)
    else:
        logging.info(f'File {soundfile} not cached. Downloading...')
        download(ru_word, FILE_CACHE)
        play(filepath)

def play(filepath):
    if os.path.isfile(filepath):
        logging.debug(f'Playing file {filepath}')
        os.system(PLAY_SOUND_CMD.format(file=filepath))
    else:
        logging.warning(f'Could not find {filepath}')

def download(ru_word, dirpath):
    filepath_template = os.path.join(FILE_CACHE, ru_word + '{suffix}' + SOUND_FILE_EXT)
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
            suffix = '' if i == 0 else f'_{i}'
            filepath = filepath_template.format(suffix=suffix)
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
            '-d', '--debug', action='store_true', help='print debug messages')
    args = parser.parse_args()
    log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    _setup_logging(log_level)
    _setup_file_cache()
    pronounce(args.ru_word)
