"""Storage errors. Callers must not catch a bare Exception to hide jail failures."""


class StorageError(Exception):
    """Base class for FileStore / BlobStore failures."""


class NotFound(StorageError):
    pass


class PermissionDenied(StorageError):
    pass


class NotSupported(StorageError):
    pass


class InvalidPath(StorageError):
    pass
