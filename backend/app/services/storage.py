"""Binary storage (uploads, normalized PDFs, signed PDFs) in GridFS (DECISIONS.md D8).

This is the storage interface: moving to S3 or R2 means reimplementing these functions. Blobs
are only ever reached through an id stored on a tenant document, which is read through
`repo.Tenant`, so a blob id never crosses an org boundary. Each blob also records its `org_id`.
"""

from collections import OrderedDict

from bson import ObjectId
from gridfs import AsyncGridFSBucket


async def put(
    bucket: AsyncGridFSBucket,
    data: bytes,
    *,
    filename: str,
    org_id: ObjectId,
    kind: str,
    content_type: str = "application/pdf",
) -> ObjectId:
    return await bucket.upload_from_stream(
        filename,
        data,
        metadata={"org_id": org_id, "kind": kind, "content_type": content_type},
    )


async def get(bucket: AsyncGridFSBucket, blob_id: ObjectId) -> bytes:
    stream = await bucket.open_download_stream(blob_id)
    return await stream.read()


async def delete(bucket: AsyncGridFSBucket, blob_id: ObjectId) -> None:
    await bucket.delete(blob_id)


class BytesLRU:
    """A small in-process cache bounded by total size. Blobs are immutable (a changed PDF gets a
    new blob id), so entries never go stale."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple[object, ...], bytes] = OrderedDict()
        self._size = 0

    def get(self, key: tuple[object, ...]) -> bytes | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: tuple[object, ...], value: bytes) -> None:
        if len(value) > self.max_bytes // 4:
            return  # one huge entry would evict everything else
        if key in self._items:
            self._size -= len(self._items.pop(key))
        self._items[key] = value
        self._size += len(value)
        while self._size > self.max_bytes:
            _, evicted = self._items.popitem(last=False)
            self._size -= len(evicted)
