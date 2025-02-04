import datetime
import os

import async_hvac
from aiohttp import web
from marshmallow import Schema, fields, validate
from webargs.aiohttpparser import use_args

import settings

CUBBYHOLE_PATH = os.path.join(settings.VAULT_SECRET_BASE, "secret")


class SecretField(Schema):
    name = fields.Str(validate=validate.Length(max=100, error="String is too long"))
    value = fields.Str(validate=validate.Length(max=100, error="String is too long"))

    class Meta:
        strict = True


class Secret(Schema):
    name = fields.Str(validate=validate.Length(max=100, error="String is too long"))
    fields = fields.Nested(SecretField, many=True)

    class Meta:
        strict = True


async def new_cubbyhole(secret, master_client):
    """
    Create a new token with a lifetime of a week, and with it store the secret
    in a cubbyhole.
    """
    token = await master_client.create_token(
        policies=[settings.VAULT_POLICY], lease="168h", meta={"name": secret["name"]}
    )
    client = async_hvac.AsyncClient(
        settings.VAULT_ADDR, token=token["auth"]["client_token"]
    )
    await client.write(
        CUBBYHOLE_PATH, lease=f"{7 * 24}h", fields=[dict(f) for f in secret["fields"]]
    )
    await client.close()
    return token


@use_args(Secret)
async def new_secret(request, secret: Secret):
    token = await new_cubbyhole(secret, request.config_dict["vault_master"])
    return web.json_response(
        {
            "url": str(
                request.app.router["show_secret"].url_for(
                    token=token["auth"]["client_token"]
                )
            ),
            "token": token["auth"]["client_token"],
            "expiration": (
                datetime.datetime.now()
                + datetime.timedelta(seconds=token["auth"]["lease_duration"])
            ).isoformat(),
        }
    )


@use_args({"token": fields.Str(required=True, location="match_info")})
async def show_secret(request, kwargs):
    token = kwargs["token"]
    client = async_hvac.AsyncClient(settings.VAULT_ADDR, token=token)
    try:
        return web.json_response(await client.lookup_token())
    except async_hvac.exceptions.Forbidden:
        raise web.HTTPNotFound()
    finally:
        await client.close()


@use_args({"token": fields.Str(required=True, location="match_info")})
async def show_secret_contents(request, kwargs):
    token = kwargs["token"]
    client = async_hvac.AsyncClient(settings.VAULT_ADDR, token=token)
    try:
        return web.json_response(await client.read(CUBBYHOLE_PATH))
    except async_hvac.exceptions.Forbidden:
        raise web.HTTPNotFound()
    finally:
        await client.close()


routes = [
    web.post("/new", handler=new_secret),
    web.get("/show/{token}", handler=show_secret, name="show_secret"),
    web.get("/show/{token}/contents", handler=show_secret_contents),
]

app = web.Application()
app.add_routes(routes)
