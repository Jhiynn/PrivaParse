"""What a protocol adapter is, and the ones this gateway serves.

A protocol adapter is everything the gateway knows about one wire protocol:
where text sits in a request, where it sits in an answer, and the path it is
served at. The five things that used to vary between two copies of the same
route are its fields, so a per-protocol difference now has to be *declared*
here rather than arrived at by editing one route and forgetting the other.

The callable fields are declared as callback protocols rather than as bare
`Callable`, and that is the load-bearing part. The failure this shape exists
to prevent is one adapter's request walk taking a parameter another's does
not: `gateway_allow_images` reached one route out of two for exactly that
reason, and nothing said so. A declared signature makes it a type error where
the adapter is built.

The values live in this module rather than beside each protocol's own walks
because `stream_responses.py` imports the Responses answer walk; an adapter
value in `adapter/responses.py` would have to import that relay back, and the
two modules would then import each other. Nothing inside the adapter package
imports this module, so no cycle forms.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from privaparse.gateway.adapter import openai, responses
from privaparse.gateway.extract import TextNode
from privaparse.gateway.stream import restore_sse
from privaparse.gateway.stream_responses import restore_responses_sse

__all__ = [
    "ADAPTERS",
    "CHAT_COMPLETIONS",
    "RESPONSES",
    "AnswerWalk",
    "HintInsertion",
    "ProtocolAdapter",
    "RequestWalk",
    "StreamRelay",
]


class RequestWalk(Protocol):
    """Every scannable string in a request, in a stable order. Fails closed.

    Raises `UnscannableField` on anything the walk has no rule for -- which is
    what makes the rule a property of the gateway rather than of one route.

    `allow_images` is part of the signature rather than of one protocol's,
    because a setting an operator sets once has to mean the same thing on
    every route the gateway serves.
    """

    def __call__(self, body: Any, *, allow_images: bool) -> list[TextNode]: ...


class AnswerWalk(Protocol):
    """Every restorable string in an answer. Never raises.

    The asymmetry with `RequestWalk` is deliberate: refusing on the way out
    protects a person, refusing on the way back only costs the caller an
    answer they have already paid for.
    """

    def __call__(self, body: Any) -> list[TextNode]: ...


class HintInsertion(Protocol):
    """A copy of the request with the placeholder hint added, once.

    A function rather than a field naming where the hint goes: the protocols
    disagree about what to add it to -- a message, an input item, a top-level
    string -- and forcing all of them into one shape is how the next protocol
    ends up unable to express itself.
    """

    def __call__(self, body: dict) -> dict: ...


class StreamRelay(Protocol):
    """Relay a streamed answer, putting the real values back as it passes.

    The whole relay rather than a per-event rewrite, so SSE never enters this
    contract: a protocol that frames its stream differently supplies its own
    relay instead of being reshaped to fit somebody else's framing.
    """

    def __call__(
        self,
        chunks: AsyncIterator[bytes],
        *,
        restore: Callable[[str], Awaitable[str]],
        max_hold: int,
        lenient: bool,
    ) -> AsyncIterator[bytes]: ...


@dataclass(frozen=True)
class ProtocolAdapter:
    """One wire protocol, as a value the route body can be pointed at.

    Frozen, and deliberately without defaults on any field. A default would
    make the slot optional at construction -- which is the same silence this
    type exists to break -- and a callable default would live on the class,
    where attribute access binds it as a method.
    """

    #: How a refusal names this protocol in the log, so an operator reading it
    #: can tell which client hit it without reproducing anything.
    name: str
    #: Where it is served. Every route the gateway mounts for a provider
    #: protocol comes from this field.
    path: str
    request_walk: RequestWalk
    answer_walk: AnswerWalk
    hint_insertion: HintInsertion
    stream_relay: StreamRelay


CHAT_COMPLETIONS = ProtocolAdapter(
    name="chat completions",
    path="/v1/chat/completions",
    request_walk=openai.extract_request,
    answer_walk=openai.extract_answer,
    hint_insertion=openai.with_placeholder_hint,
    stream_relay=restore_sse,
)

#: The Responses API. Codex CLI speaks only this one -- `wire_api = "chat"`
#: was removed in February 2026 -- so a Chat Completions gateway cannot serve
#: it at all.
RESPONSES = ProtocolAdapter(
    name="responses",
    path="/v1/responses",
    request_walk=responses.extract_request,
    answer_walk=responses.extract_answer,
    hint_insertion=responses.with_placeholder_hint,
    stream_relay=restore_responses_sse,
)

#: Every protocol the gateway serves. `create_app` mounts one route per entry,
#: so a third protocol is one more line here rather than a third copy of the
#: route. A tuple and not a registry on purpose: nothing discovers an adapter,
#: and the set the gateway serves is readable in one place without running it.
ADAPTERS: tuple[ProtocolAdapter, ...] = (CHAT_COMPLETIONS, RESPONSES)
