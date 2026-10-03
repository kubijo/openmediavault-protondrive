"""Publish immutable VM bases atomically, with serialized builders and explicit inputs."""

import fcntl
import hashlib
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def fingerprint(path: Path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class BaseCache:
    def __init__(self, directory: Path, inputs: dict):
        self.inputs = inputs
        self.key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        self.directory = directory.resolve() / self.key

    def current(self):
        pointer = self.directory / 'current.json'
        if not pointer.exists():
            return None
        try:
            metadata = json.loads(pointer.read_text())
            if not isinstance(metadata['generation'], str):
                return None
            generation = str(uuid.UUID(hex=metadata['generation'])).replace('-', '')
            image = self.directory / generation / 'base.qcow2'
            if metadata['inputs'] == self.inputs and image.is_file() and image.stat().st_size == metadata['size']:
                return image
        except (ValueError, KeyError, TypeError):
            pass
        return None

    def ensure(self, build, *, refresh=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'build.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not refresh and (image := self.current()) is not None:
                return image, False
            generation = uuid.uuid4().hex
            destination = self.directory / generation
            with tempfile.TemporaryDirectory(prefix='.building-', dir=self.directory) as temporary:
                staging = Path(temporary)
                image = staging / 'base.qcow2'
                build(image)
                if not image.is_file() or not image.stat().st_size:
                    raise RuntimeError('VM base builder did not produce an image')
                metadata = {
                    'generation': generation,
                    'inputs': self.inputs,
                    'created': datetime.now(timezone.utc).isoformat(),
                    'size': image.stat().st_size,
                }
                image.chmod(0o444)
                (staging / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
                staging.rename(destination)
            # Old generations remain valid backing files for already-running guests.
            with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as stream:
                pointer = Path(stream.name)
                json.dump(metadata, stream, indent=2)
                stream.write('\n')
            try:
                pointer.replace(self.directory / 'current.json')
            finally:
                pointer.unlink(missing_ok=True)
            return destination / 'base.qcow2', True
