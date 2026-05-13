"""CLI client for Agent Messenger — quick testing and manual messaging."""

import asyncio
import click
import aiohttp
import json


SERVER_URL = "http://localhost:8096"


@click.group()
@click.option("--server", default=SERVER_URL, help="Messenger server URL")
@click.pass_context
def cli(ctx, server):
    ctx.ensure_object(dict)
    ctx.obj["server"] = server.rstrip("/")


@cli.command()
@click.argument("agent_id")
@click.argument("name")
@click.option("--type", "agent_type", default="detached")
@click.pass_context
def register(ctx, agent_id, name, agent_type):
    """Register a new agent."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{ctx.obj['server']}/api/agents/register", json={
                "id": agent_id, "name": name, "type": agent_type,
            }) as resp:
                click.echo(json.dumps(await resp.json(), indent=2))
    asyncio.run(_run())


@cli.command()
@click.pass_context
def agents(ctx):
    """List all agents."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ctx.obj['server']}/api/agents") as resp:
                data = await resp.json()
                for a in data.get("agents", []):
                    status_icon = "🟢" if a["status"] == "online" else "⚪"
                    click.echo(f"  {status_icon} {a['id']:20s} {a['name']:20s} ({a['type']})")
    asyncio.run(_run())


@cli.command()
@click.argument("agent_id")
@click.argument("recipient_id")
@click.argument("content")
@click.pass_context
def send(ctx, agent_id, recipient_id, content):
    """Send a DM to another agent."""
    async def _run():
        from client.sdk import MessengerClient
        client = MessengerClient(agent_id, server_url=ctx.obj["server"])
        await client.connect()
        msg = await client.send_dm(recipient_id, content)
        click.echo(f"✅ Sent: {msg}")
        await client.disconnect()
    asyncio.run(_run())


@cli.command()
@click.argument("conversation_id")
@click.option("--limit", default=20)
@click.pass_context
def history(ctx, conversation_id, limit):
    """View message history for a conversation."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ctx.obj['server']}/api/messages/conversation/{conversation_id}",
                params={"limit": limit},
            ) as resp:
                data = await resp.json()
                for m in data.get("messages", []):
                    sender = m.get("sender_id", "?")
                    content = m.get("content", "")
                    ts = m.get("created_at", "")[:19]
                    click.echo(f"  [{ts}] {sender}: {content}")
    asyncio.run(_run())


@cli.command()
@click.pass_context
def feed(ctx):
    """View global message feed."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ctx.obj['server']}/dashboard/feed") as resp:
                data = await resp.json()
                for m in data.get("messages", []):
                    sender = m.get("sender_name") or m.get("sender_id", "?")
                    content = m.get("content", "")
                    ts = m.get("created_at", "")[:19]
                    click.echo(f"  [{ts}] {sender}: {content}")
    asyncio.run(_run())


@cli.command()
@click.pass_context
def stats(ctx):
    """Show server stats."""
    async def _run():
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ctx.obj['server']}/dashboard/stats") as resp:
                data = await resp.json()
                s = data.get("stats", {})
                click.echo(f"  Agents: {s.get('agents', 0)} ({s.get('online', 0)} online)")
                click.echo(f"  Conversations: {s.get('conversations', 0)}")
                click.echo(f"  Messages: {s.get('messages', 0)}")
                click.echo(f"  Online now: {', '.join(data.get('online_agents', [])) or 'none'}")
    asyncio.run(_run())


def main():
    cli()


if __name__ == "__main__":
    main()
