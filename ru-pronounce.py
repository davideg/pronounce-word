import argparse
import base64
import logging
import os
import re
import requests
import json
import concurrent.futures
import sys

FILE_CACHE = os.path.expanduser('~/.ru-pronounce/cache/')
WORD_DATA_FILE = os.path.expanduser('~/.ru-pronounce/word-data.json')
PLAY_SOUND_CMD = 'afplay "{file}"'
SOUND_FILE_EXT = '.mp3'
FORVO_URL = 'https://forvo.com/word/{ru_word}/#ru'
AUDIO_URL = 'https://audio00.forvo.com/audios/mp3/{path}'
FIND_ENCODED_AUDIO_ARGS_RE = 'Play\((\d+,[^)]*)'
FALLBACK_AUDIO_URL = 'https://audio00.forvo.com/mp3/{path}'
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36'
MAX_FILES_PER_WORD = 20


def _setup_logging(log_level):
    logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=log_level)

def _setup_file_cache():
    if not os.path.isdir(FILE_CACHE):
        os.makedirs(FILE_CACHE, exist_ok=True)

class PronounceWord:

    def __init__(self,
            file_cache_dir=FILE_CACHE,
            word_data_file=WORD_DATA_FILE,
            ):
        self.file_cache_dir = file_cache_dir
        self.word_data_file = word_data_file
        self._filepath_template = os.path.join(file_cache_dir,
                '{word}'
                + '{suffix}'
                + SOUND_FILE_EXT)

        # word data format:
        # {'<word>':
        #   {
        #       'num_pronounciations': '<num>',
        #       'cycle_index': '<num>',
        #       'speaker_sex': 'm' | 'f',
        #       'speaker_location': '<str>',
        #       'disabled': True | False,
        #   },...
        # }
        self._word_data = {}

    def setup(self, rebuild_metadata=False, override=False):
        self.load_word_data()
        if rebuild_metadata:
            self._rebuild_word_data(override=override)
    
    def teardown(self):
        self.save_word_data()

    def download_if_not_available(self, ru_word, use_threads=True):
        filepath = self._get_word_filepath(ru_word)
        if not os.path.isfile(filepath):
            logging.info(f'File {filepath} not cached. Downloading...')
            self.download(ru_word, use_threads)
        else:
            logging.debug(f'Found existing {filepath}')

    def _rebuild_word_data(self, override=False):
        if override:
            # clear metadata if we're going to override it from the filesystem
            self._word_data = {}
        get_word_re = '([^_\d\.]*)(?:_\d+)?\.mp3'
        word_counts = {}
        for filename in os.listdir(self.file_cache_dir):
            match = re.match(get_word_re, filename)
            if match is not None:
                word = match.group(1)
                if word not in self._word_data or override:
                    if word in word_counts:
                        word_counts[word] += 1
                    else:
                        word_counts[word] = 1
        for word, count in word_counts.items():
            if word not in self._word_data or override:
                self._initialize_word_metadata(word, override)
                self._word_data[word]['num_pronounciations'] = count

    def save_word_data(self):
        with open(self.word_data_file, 'w') as fd:
            json.dump(self._word_data, fd)

    def load_word_data(self):
        if os.path.isfile(self.word_data_file):
            with open(self.word_data_file, 'r') as fd:
                self._word_data = json.load(fd)
        else:
            logging.warning('Could not load word data.'
                    f' File {self.word_data_file} does not exist.')

    def _get_word_filepath(self, ru_word, index=0):
        suffix = '' if index == 0 else f'_{index}'
        return self._filepath_template.format(word=ru_word, suffix=suffix)

    def cycle_pronounciations(self, ru_word, num_to_cycle=-1):
        num_pronounciations = self._word_data[ru_word]['num_pronounciations']
        cycle_index = self._word_data[ru_word]['cycle_index']
        if num_to_cycle > 0:
            num_to_cycle = min(num_pronounciations, num_to_cycle)
        else:
            num_to_cycle = num_pronounciations
        # if we're given a smaller number to cycle through start the cycle over
        if cycle_index > (num_to_cycle - 1):
            cycle_index = 0
        self.pronounce(ru_word, cycle_index)
        self._word_data[ru_word]['cycle_index'] = (cycle_index + 1) % num_to_cycle

    def pronounce(self, ru_word, pronunciation_index=0):
        filepath = self._get_word_filepath(ru_word, pronunciation_index)
        self.play(filepath)

    def play(self, filepath):
        if os.path.isfile(filepath):
            logging.debug(f'Playing file {filepath}')
            os.system(PLAY_SOUND_CMD.format(file=filepath))
        else:
            logging.error(f'File {filepath} does not exist')

    def _initialize_word_metadata(self, ru_word, override=False):
        if ru_word not in self._word_data or override:
            word_metadata = {
                    'num_pronounciations': 0,
                    'cycle_index': 0,
                    'speaker_sex': None,
                    'speaker_location': None,
                    'disabled': False
                    }
            self._word_data[ru_word] = word_metadata

    def _clear_word_metadata(self, ru_word):
        if ru_word in self._word_data:
            del self._word_data[ru_word]

    def _download_file(self, ru_word, index, url, headers):
        logging.debug(f'Downloading {url}...')
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            filepath = self._get_word_filepath(ru_word, index)
            logging.debug(f'Download successful. Saving to {filepath}')
            with open(filepath, 'wb') as fd:
                fd.write(r.content)
            return True
        else:
            logging.warning(f'Problem downloading {url}!'
                          f' Status code: {r.status_code}')
            return False

    def download(self, ru_word, use_threads=True):
        self._initialize_word_metadata(ru_word)
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
        else:
            logging.error(f'Could not find pronounciations for {ru_word}')
            logging.debug(f'Clearing word metadata for {ru_word}')
            self._clear_word_metadata(ru_word)
            # this means the word could not be found, so let's exit
            sys.exit(1)

        if use_threads:
            # Download files with a threadpool
            num_to_download = min(len(audio_urls), MAX_FILES_PER_WORD)
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_to_download) as executor:
                futures = [executor.submit(self._download_file, ru_word, i, url, headers)
                        for i, url in enumerate(audio_urls[:num_to_download])]
                results = [f.result() for f in futures]

            successful_dls = sum(results)
        else:
            successful_dls = 0
            for i, url in enumerate(audio_urls):
                success = self._download_file(ru_word, i, url, headers)
                if success:
                    successful_dls += 1
                if i + 1 >= MAX_FILES_PER_WORD:
                    break

        self._word_data[ru_word]['num_pronounciations'] = successful_dls
        #TODO extract metadata about speaker sex and location


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
            default=0,
            help='play the nth available word pronounciation' \
            ' where n is the integer provided to this option'
            )
    group.add_argument(
            '-r',
            '--random',
            action='store_true',
            help='play a random available word pronounciation'
            )
    parser.add_argument(
            '--disable-threading',
            action='store_true',
            help='skips using threads when downloading files'
            )
    metadata_group = parser.add_argument_group('metadata')
    metadata_group.add_argument(
            '--rebuild-metadata',
            action='store_true',
            help='rebuilds word metadata from cached audio files'
            )
    metadata_group.add_argument(
            '--override',
            action='store_true',
            help='overrides existing word metadata when rebuilding metadata'
            )
    args = parser.parse_args()
    if args.override and not args.rebuild_metadata:
        parser.error('--rebuild-metadata must be used when specifying --override')
    log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    _setup_logging(log_level)
    _setup_file_cache()
    pronouncer = PronounceWord(
            file_cache_dir=FILE_CACHE,
            word_data_file=WORD_DATA_FILE)
    pronouncer.setup(rebuild_metadata=args.rebuild_metadata,
            override=args.override)
    use_threads = not args.disable_threading
    ru_word = args.ru_word.strip().lower()
    pronouncer.download_if_not_available(ru_word, use_threads=use_threads)
    if args.cycle is not None:
        # cycle pronounciations
        pronouncer.cycle_pronounciations(ru_word, num_to_cycle=args.cycle)
    elif args.random:
        pronouncer.play_random_pronounciation(ru_word)
    else:
        pronouncer.pronounce(ru_word, pronunciation_index=args.play_n)
    pronouncer.teardown()
