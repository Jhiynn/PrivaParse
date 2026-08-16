"""What every protocol adapter needs, and no protocol owns.

A protocol adapter's own module holds everything the gateway knows about one
wire protocol. Anything two of them would otherwise have to share -- by one
importing the other, which makes the older adapter the special case the newer
one has to imitate -- lives here instead.
"""

from __future__ import annotations

__all__ = ["PLACEHOLDER_HINT"]

# The opt-in outbound hint. Deliberately short: it is prepended to somebody
# else's prompt, and every token of it is billed to them. It names the shape
# rather than explaining the scheme, because the provider does not need to be
# told what the placeholders stand for.
#
# The wording is shared rather than per-protocol on purpose. It says nothing
# about messages or items, so no protocol has a reason to reword it, and one
# copy means an improvement to it reaches every route at once. Where the
# protocols do differ -- a system message, a system input item, a top-level
# string -- is in the insertion, which stays each adapter's own function.
PLACEHOLDER_HINT = (
    "Some values in this conversation are replaced by privacy placeholders of the "
    "form [[TYPE_A1]]. Reproduce any such token exactly as written, character for "
    "character, including both pairs of square brackets. Never translate, rename, "
    "reformat, quote, split or omit one."
)
