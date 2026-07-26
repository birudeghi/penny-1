"""`GET /api/media/{message_id}` — handing a family's own photograph back to them.

THIS IS THE HIGHEST-CONSEQUENCE TENANT BOUNDARY IN THE PRODUCT. Every other leak in this API
exposes a title, a date or a sentence. This one exposes a photograph of an 84-year-old's body,
taken by their daughter to show a district nurse, to whoever asked for it. There is no
recovering from that, for the family or for us — so this route is written as if the id in the
path were hostile, because one day it will be.

FOUR THINGS MAKE IT SAFE, AND EACH ONE ALONE WOULD BE ENOUGH IF THE OTHERS WERE PERFECT.

1. **The same dependency every other read uses.** `CurrentHousehold` is the whole tenancy
   mechanism in this app — signed cookie in, `HouseholdCtx` out, `where(household_id == ctx.id)`
   on every query. This route invents nothing of its own: a bespoke check here would be a
   second mechanism to keep in step with `app.deps`, and the one that drifts is always the one
   guarding the sensitive thing.

2. **404, never 403, and byte-identical to a missing id.** Another household's message id, a
   message with no file, a random UUID and a household that was deleted five minutes ago all
   render `{"detail": "Not found."}` with the same status. A 403 would be an existence oracle:
   "that message is real, it just isn't yours" is exactly the sentence an attacker enumerating
   ids is trying to buy. `NotFoundError` takes no message precisely so no future edit can make
   one of these distinguishable — see `app.errors`.

3. **The path is built from the SESSION, not from the row.** `stored_file(ctx.id, message_id)`
   resolves `<media_root>/<household_id>/<message_id>` from the household id the cookie proved,
   never one read off the message. So even a query
   that lost its tenant predicate in some future refactor could still only ever resolve to a
   file inside the caller's own directory. Defence in depth that costs one argument.

4. **The content type is the one we recorded, mapped through a fixed table — never sniffed.**
   These bytes came from a stranger's phone by way of a group chat. Letting the file decide
   what it is is how a "photo" is served as `text/html` and runs script on our origin with the
   session cookie sitting next to it. `app.media.served_as` answers with an image type for the
   four we render inline and `application/octet-stream` as an attachment for everything else;
   the app-wide `nosniff` header is what stops a browser second-guessing that.

NO GUESSABLE ALIAS, EITHER. The id in the path is `messages.id`, a v4 UUID that was already
generated for the row — 122 bits of randomness that cannot be walked. Adding a short share
token or a sequential media number would be adding the enumerable identifier this route
carefully does not have.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.deps import CurrentHousehold, SessionDep
from app.errors import NotFoundError
from app.media import served_as, stored_file
from app.models import Message

router = APIRouter(prefix="/api", tags=["media"])

# A photograph of somebody's body must not sit in the disk cache of the shared family laptop
# after they sign out, and must never be held by a proxy. The cost is one authenticated request
# per view, which is the correct price for this.
CACHE_CONTROL = "private, no-store, max-age=0"


@router.get("/media/{message_id}")
async def get_media(
    message_id: UUID,
    ctx: CurrentHousehold,
    session: SessionDep,
) -> FileResponse:
    """The stored attachment for one message. 404 for everything that is not the caller's.

    Note what is NOT selected: `household_id`. It is a predicate here, never a value — nothing
    below this line reads a household id out of the row, so there is no way for the answer to
    be shaped by the message rather than by the session.
    """
    recorded = (
        await session.execute(
            sa.select(Message.media_content_type).where(
                Message.id == message_id,
                # THE LINE THIS ROUTE EXISTS TO GET RIGHT.
                Message.household_id == ctx.id,
                # "We kept bytes for this message" is exactly this column being non-null, and
                # it is a PREDICATE rather than a check on the value that comes back. Without
                # it, a text message of ours and a message that does not exist both arrive here
                # as `None` and are told apart only by the reader's memory of which is which —
                # and the two must be indistinguishable in the response anyway, so the query
                # may as well make that structural.
                Message.media_content_type.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if recorded is None:
        # Three different situations, one response: no such message, someone else's message,
        # or a message of ours that has no file. They must stay indistinguishable.
        raise NotFoundError

    path = await stored_file(ctx.id, message_id)
    if path is None:
        # The row says there are bytes and the volume disagrees — a restored snapshot, a
        # reattached empty volume, a manual clean-up. Still a 404, and still the same one: an
        # error here would tell a caller that the message exists.
        raise NotFoundError

    served = served_as(recorded, message_id)
    return FileResponse(
        path,
        media_type=served.content_type,
        filename=served.filename,
        content_disposition_type=served.disposition,
        headers={"Cache-Control": CACHE_CONTROL},
    )
