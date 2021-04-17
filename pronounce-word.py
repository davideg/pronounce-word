import argparse
import base64
import logging
import os
import re
import requests
import json
import concurrent.futures
import sys
import time
import random

FILE_CACHE = os.path.expanduser('~/.pronounce-word/cache/')
WORD_DATA_FILE = os.path.expanduser('~/.pronounce-word/word-data.json')
PLAY_SOUND_CMD = 'afplay "{file}"'
SOUND_FILE_EXT = '.mp3'
FORVO_URL = 'https://forvo.com/word/{word}/'
AUDIO_URL = 'https://audio00.forvo.com/audios/mp3/{path}'
FIND_ENCODED_URLS_AND_SPEAKER_INFO_RE = r'Play\((\d+,[^)]*)\).*?<span class="from">\((.+?)(?: from ([^)]+?))?\)'
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
        #       'audio_urls': ['<url>', '<url>', ...],
        #       'speaker_info': [('<sex>', '<location>'), ('m' | 'f', '<str>'),...],
        #       'disabled': [True | False, ...],
        #       'downloaded': [True | False, ...]
        #   },...
        # }
        self._word_data = {}
        self._force_download = False

    def setup(self, rebuild_metadata=False, override=False, force_download=False):
        self.load_word_data()
        if rebuild_metadata:
            self._rebuild_word_data(override=override)
        if force_download:
            self._force_download = True
    
    def teardown(self):
        self.save_word_data()

    def _download_if_not_available(self, word, index):
        filepath = self._get_word_filepath(word, index)
        if not os.path.isfile(filepath) or self._force_download:
            if self._force_download:
                logging.debug('Forcing download')
            else:
                logging.info(f'File {filepath} not cached. Downloading...')
            url = self._word_data[word]['audio_urls'][index]
            headers = {
                'User-Agent': USER_AGENT
            }
            success = self._download_file(word, index, url, headers)
            return success
        else:
            logging.debug(f'Found existing {filepath}')
            return True

    def _rebuild_word_data(self, override=False):
        logging.debug('Rebuilding word metadata using filesystem')
        if override:
            # clear metadata if we're going to override it from the filesystem
            self._word_data = {}
        get_word_re = '([^_\d\.]*)(?:_(\d+))?\.mp3'
        num_seen = {}
        for filename in os.listdir(self.file_cache_dir):
            match = re.match(get_word_re, filename)
            if match is not None:
                word = match.group(1)
                num = match.group(2)
                if num is None:
                    num = 0
                else:
                    num = int(num)
                if word not in num_seen:
                    num_seen[word] = []
                num_seen[word].append(num)
            else:
                logging.warning('Problem finding downloaded files')
        for word, nums in num_seen.items():
            if word not in self._word_data or override:
                # Total pronounciations should be 1 greater than the
                # highest index we've encountered
                num_pronounciations = max(nums) + 1
                self._initialize_word_metadata(word, override)
                self._word_data[word]['num_pronounciations'] = num_pronounciations
                self._word_data[word]['disabled'] = [False] * num_pronounciations
                self._word_data[word]['downloaded'] = [False] * num_pronounciations
                for num in nums:
                    self._word_data[word]['downloaded'][num] = True
            if len(self._word_data[word]['audio_urls']) \
                    < self._word_data[word]['num_pronounciations']:
                logging.debug(f'Missing metadata for "{word}". Gathering metadata again.')
                downloaded = self._word_data[word]['downloaded']
                # if for whatever reason we're missing audio URLs let's get them again
                self._populate_word_metadata(word, override=True)
                self._word_data[word]['downloaded'] = downloaded
                # sleep so we don't bombard Forvo's servers
                time.sleep(random.random()*1.5 + 1)

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

    def _get_word_filepath(self, word, index=0):
        suffix = '' if index == 0 else f'_{index}'
        return self._filepath_template.format(word=word, suffix=suffix)

    def populate_word_metadata_or_exit(self, word):
        if not self._populate_word_metadata(word):
            # this means the word could not be found, so let's exit
            sys.exit(1)

    def cycle_pronounciations(self, word, num_to_cycle=-1):
        self.populate_word_metadata_or_exit(word)
        num_pronounciations = self._word_data[word]['num_pronounciations']
        cycle_index = self._word_data[word]['cycle_index']
        if num_to_cycle > 0:
            num_to_cycle = min(num_pronounciations, num_to_cycle)
        else:
            num_to_cycle = num_pronounciations
        # if we're given a smaller number to cycle through start the cycle over
        if cycle_index > (num_to_cycle - 1):
            cycle_index = 0
        self.pronounce(word, cycle_index)
        self._word_data[word]['cycle_index'] = (cycle_index + 1) % num_to_cycle

    def pronounce(self, word, pronunciation_index=0):
        self.populate_word_metadata_or_exit(word)
        if self._download_if_not_available(word, pronunciation_index):
            filepath = self._get_word_filepath(word, pronunciation_index)
            self.play(filepath)
            self._download_remaining_if_not_available(word, index_to_skip=pronunciation_index)
        else:
            # download was not successful so exit
            sys.exit(1)

    def play(self, filepath):
        if os.path.isfile(filepath):
            logging.debug(f'Playing file {filepath}')
            os.system(PLAY_SOUND_CMD.format(file=filepath))
        else:
            logging.error(f'File {filepath} does not exist')

    def _initialize_word_metadata(self, word, override=False):
        if word not in self._word_data or override:
            word_metadata = {
                    'num_pronounciations': 0,
                    'cycle_index': 0,
                    'audio_urls': [],
                    'speaker_info': [],
                    'disabled': [],
                    'downloaded': []
                    }
            self._word_data[word] = word_metadata

    def _clear_word_metadata(self, word):
        if word in self._word_data:
            del self._word_data[word]

    def _download_file(self, word, index, url, headers):
        logging.debug(f'Downloading {url}...')
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            filepath = self._get_word_filepath(word, index)
            logging.debug(f'Download successful. Saving to {filepath}')
            with open(filepath, 'wb') as fd:
                fd.write(r.content)
            return True
        else:
            logging.warning(f'Problem downloading {url}!'
                          f' Status code: {r.status_code}')
            return False

    def _fetch_audio_urls_and_speaker_info(self, word):
        audio_urls = []
        speaker_info = []
        headers = {
            'User-Agent': USER_AGENT
        }
        regex = re.compile(FIND_ENCODED_URLS_AND_SPEAKER_INFO_RE, re.DOTALL)
        r = requests.get(FORVO_URL.format(word=word), headers=headers)
        if r.status_code == 200:
            matches = regex.findall(r.text)
            for args, sex, location in matches:
                # the first part of each match is an arguments list to a function.
                args_list = [arg.strip('\'') for arg in args.split(',')]
                if args_list and args_list[4] != '':
                    converted_match = base64.b64decode(args_list[4]).decode('utf-8')
                    audio_url = AUDIO_URL.format(path=converted_match)
                    audio_urls.append(audio_url)
                elif args_list and args_list[1] != '':
                    converted_match = base64.b64decode(args_list[1]).decode('utf-8')
                    audio_url = FALLBACK_AUDIO_URL.format(path=converted_match)
                    audio_urls.append(audio_url)
                speaker_info.append((sex, location))
            return audio_urls, speaker_info
        else:
            logging.error(f'Could not find pronounciations for {word}')
            return None, None

    def _populate_word_metadata(self, word, override=False):
        if word not in self._word_data or override:
            if override:
                logging.debug(f'Overriding existing metadata for "{word}"')
            logging.debug(f'Populating metadata for "{word}"')
            self._initialize_word_metadata(word, override=override)
            audio_urls, speaker_info = self._fetch_audio_urls_and_speaker_info(word)
            if audio_urls is None:
                logging.debug(f'Clearing word metadata for {word}')
                self._clear_word_metadata(word)
                return False
            num_pronounciations = min(len(audio_urls), MAX_FILES_PER_WORD)
            self._word_data[word]['num_pronounciations'] = num_pronounciations
            self._word_data[word]['audio_urls'] = audio_urls[:num_pronounciations]
            self._word_data[word]['speaker_info'] = speaker_info
            self._word_data[word]['disabled'] = [False] * num_pronounciations
            self._word_data[word]['downloaded'] = [False] * num_pronounciations
        if logging.getLogger().level <= logging.DEBUG // 2:
            metadata = self._word_data[word]
            logging.debug(f'Metadata for "{word}":')
            logging.debug(f'"num_pronounciations": {metadata["num_pronounciations"]}')
            logging.debug(f'"audio_urls": {metadata["audio_urls"]}')
            logging.debug(f'"speaker_info": {metadata["speaker_info"]}')
            logging.debug(f'"disabled": {metadata["disabled"]}')
            logging.debug(f'"downloaded": {metadata["downloaded"]}')
        return True

    def _audit_downloaded(self, word):
        '''Returns number of files remaining to be downloaded'''
        logging.debug(f'Checking downloaded files for "{word}"...')
        self._word_data[word]['downloaded'] = [os.path.isfile(
                self._get_word_filepath(word, index))
                for index in range(self._word_data[word]['num_pronounciations'])]
        logging.debug(f'There are {sum(self._word_data[word]["downloaded"])}'
                f' files out of {len(self._word_data[word]["downloaded"])}.')
        return len(self._word_data[word]["downloaded"]) - sum(self._word_data[word]["downloaded"])

    def _download_remaining_if_not_available(self, word, index_to_skip=0):
        if all(self._word_data[word]['downloaded']) and not self._force_download:
            logging.debug('Metadata indicates there are no remaining'
                    f' files to download for "{word}".')
            return
        headers = {
            'User-Agent': USER_AGENT
        }
        remaining_to_download = self._audit_downloaded(word)
        if remaining_to_download == 0 and not self._force_download:
            logging.debug(f'No files remaining to download for "{word}"')
            return
        if self._force_download:
            remaining_to_download = self._word_data[word]['num_pronounciations']
            # reset 'downloaded' metadata to force the files to download
            self._word_data[word]['downloaded'] = [False]*remaining_to_download
        audio_urls = self._word_data[word]['audio_urls']
        logging.debug(f'Downloading {remaining_to_download} files for "{word}"')

        # Use default max_workers for ThreadPoolExecutor (by not specifying a value)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [(i, executor.submit(self._download_file, word, i, url, headers))
                    for i, url in enumerate(audio_urls)
                    if i != index_to_skip
                    and not self._word_data[word]['downloaded'][i]]
            results = [(i, f.result()) for (i, f) in futures]
            for i, result in results:
                self._word_data[word]['downloaded'][i] = result

    def override_metadata(self, word):
        self._populate_word_metadata(word, override=True)
        self._audit_downloaded(word)

    def force_download(self, word):
        self._download_remaining_if_not_available(word, index_to_skip=-1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('word')
    parser.add_argument(
            '-d',
            '--debug',
            action='count',
            help='print debug messages'
            )
    parser.add_argument(
            '-f',
            '--force-download',
            action='store_true',
            help='force downloading audio files whether they exist or not'
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
    if args.rebuild_metadata or args.override:
        # ignore other args
        if args.cycle or args.play_n or args.random:
            logging.warning('When --rebuild-metadata and/or --override are'
            ' provided, --cycle, --play-n, and --random are'
            ' ignored, and only metadata operations and/or forced downloads'
            ' (when --force-download is provided) are performed')
        args.cycle = None
        args.random = False
        args.play_n = -1
    log_level = logging.INFO
    if args.debug is not None and args.debug > 0:
        log_level = logging.DEBUG // args.debug
    _setup_logging(log_level)
    _setup_file_cache()
    pronouncer = PronounceWord(
            file_cache_dir=FILE_CACHE,
            word_data_file=WORD_DATA_FILE)
    pronouncer.setup(rebuild_metadata=args.rebuild_metadata,
            force_download=args.force_download)
    word = args.word.strip().lower()
    try:
        if args.override and not args.rebuild_metadata:
            # override the metadata for the particular word
            pronouncer.override_metadata(word)
        if args.force_download:
            # download all files for the word
            pronouncer.force_download(word)
        if args.cycle is not None:
            # cycle pronounciations
            pronouncer.cycle_pronounciations(word, num_to_cycle=args.cycle)
        elif args.random:
            pronouncer.play_random_pronounciation(word)
        elif args.play_n >= 0:
            pronouncer.pronounce(word, pronunciation_index=args.play_n)
    except Exception as e:
        logging.exception(e)
    finally:
        pronouncer.teardown()
