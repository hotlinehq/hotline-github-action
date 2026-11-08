"""Which Hotline image to run."""

from .common import IMAGE_REPO


def pinned(root):
    """The reference in proxy-image.txt: the first line that is not a comment."""
    try:
        lines = (root / "proxy-image.txt").read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            return line
    return ""


def resolve(override, root):
    """Returns (reference, overridden). An override is any reference, or the
    word edge; it gives up the digest pin."""
    if override:
        if override == "edge":
            override = f"{IMAGE_REPO}:edge"
        return override, True
    ref = pinned(root)
    if not ref:
        raise RuntimeError("no image is pinned yet: run the 'pin image' workflow, or set image: edge")
    return ref, False
