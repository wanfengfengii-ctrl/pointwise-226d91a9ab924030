from writer.media import FileMedia


class InterruptedAppend(OSError):
    pass


class RecordingMedia(FileMedia):
    """Write a real prefix and record the byte interval before losing the reply."""

    def __init__(self, path, *, interrupt_after=None, lose_reply=False):
        super().__init__(path)
        self.interrupt_after = interrupt_after
        self.lose_reply = lose_reply
        self.ranges = []

    def append(self, payload):
        limit = len(payload) if self.interrupt_after is None else self.interrupt_after
        if type(limit) is not int or not 0 <= limit <= len(payload):
            raise ValueError('interruption offset falls outside the append')
        start = len(self.read_all())
        written = super().append(payload[:limit])
        self.ranges.append((start, start + written))
        if self.interrupt_after is not None or self.lose_reply:
            raise InterruptedAppend('append reply unavailable')
        return written
