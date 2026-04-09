"""
FRRouting MCP Server
Connects to an FRR container via SSH and exposes network management tools.
"""

import os
import re
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from netmiko import ConnectHandler

load_dotenv()

mcp = FastMCP("frr-mcp")

ROUTER_HOST = os.environ.get("ROUTER_HOST", "")
ROUTER_PORT = int(os.environ.get("ROUTER_PORT", "22"))
ROUTER_USER = os.environ.get("ROUTER_USER", "")
ROUTER_PASS = os.environ.get("ROUTER_PASS", "")


def _vtysh(command: str) -> str:
    """Run a single vtysh command over SSH."""
    device = {
        "device_type": "linux",
        "host": ROUTER_HOST,
        "port": ROUTER_PORT,
        "username": ROUTER_USER,
        "password": ROUTER_PASS,
    }
    with ConnectHandler(**device) as conn:
        output = conn.send_command(f"vtysh -c '{command}'")
    return output


def _vtysh_config(commands: list[str]) -> str:
    """Run a list of commands in vtysh config mode."""
    joined = "\n".join(commands)
    script = f"vtysh -c 'configure terminal' -c '{joined}' -c 'end' -c 'write memory'"
    device = {
        "device_type": "linux",
        "host": ROUTER_HOST,
        "port": ROUTER_PORT,
        "username": ROUTER_USER,
        "password": ROUTER_PASS,
    }
    with ConnectHandler(**device) as conn:
        output = conn.send_command(script)
    return output


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_ipv4(ip: str) -> None:
    if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip):
        raise ValueError(f"Invalid IPv4 address: {ip!r}")
    if any(int(p) > 255 for p in ip.split(".")):
        raise ValueError(f"IPv4 octet out of range: {ip!r}")

def _validate_mask(mask: str) -> None:
    valid = re.compile(
        r"^(255|254|252|248|240|224|192|128|0)\."
        r"(255|254|252|248|240|224|192|128|0)\."
        r"(255|254|252|248|240|224|192|128|0)\."
        r"(255|254|252|248|240|224|192|128|0)$"
    )
    if not valid.match(mask):
        raise ValueError(f"Invalid subnet mask: {mask!r}")

def _validate_interface(intf: str) -> None:
    if not re.match(r"^[a-zA-Z][\w\-\./:]*$", intf):
        raise ValueError(f"Invalid interface name: {intf!r}")

def _validate_description(desc: str) -> None:
    if len(desc) > 200:
        raise ValueError("Description must be <= 200 characters")
    if any(c in desc for c in ['`', '$', '\\', '|', ';', '&', '"', "'"]):
        raise ValueError("Description contains forbidden characters")

def _validate_prefix_len(prefix: int) -> None:
    if not (0 <= prefix <= 32):
        raise ValueError(f"Prefix length must be 0-32, got {prefix}")


# ════════════════════════════════════════════════════════════════════════════
# TOOL 1 — get_interfaces (READ)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def get_interfaces() -> dict:
    """List all interfaces with their status and IP addresses."""
    output = _vtysh("show interface brief")
    return {"output": output}


# ════════════════════════════════════════════════════════════════════════════
# TOOL 2 — get_routes (READ)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def get_routes(prefix: str = "") -> dict:
    """
    Show the IP routing table.

    Args:
        prefix: Optional specific prefix to look up e.g. '10.0.0.0/24'.
                Leave empty for full table.
    """
    if prefix:
        if "/" in prefix:
            ip, length = prefix.split("/", 1)
            _validate_ipv4(ip)
            _validate_prefix_len(int(length))
        else:
            _validate_ipv4(prefix)
        output = _vtysh(f"show ip route {prefix}")
    else:
        output = _vtysh("show ip route")
    return {"output": output}


# ════════════════════════════════════════════════════════════════════════════
# TOOL 3 — get_device_info (READ)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def get_device_info() -> dict:
    """Get device hostname, FRR version, and uptime."""
    version = _vtysh("show version")
    hostname = _vtysh("show running-config | include hostname")
    return {"version": version, "hostname": hostname}


# ════════════════════════════════════════════════════════════════════════════
# TOOL 4 — ping_device (READ / diagnostic)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def ping_device(destination: str, count: int = 5) -> dict:
    """
    Run a ping from the router to test reachability.

    Args:
        destination: Target IPv4 address.
        count:       Number of pings (1-20).
    """
    _validate_ipv4(destination)
    if not (1 <= count <= 20):
        raise ValueError("count must be between 1 and 20")
    device = {
        "device_type": "linux",
        "host": ROUTER_HOST,
        "port": ROUTER_PORT,
        "username": ROUTER_USER,
        "password": ROUTER_PASS,
    }
    with ConnectHandler(**device) as conn:
        output = conn.send_command(
            f"ping -c {count} {destination}",
            expect_string=r"\$",
            read_timeout=30,
        )
    match = re.search(r"(\d+)% packet loss", output)
    loss = int(match.group(1)) if match else None
    return {"destination": destination, "packet_loss_pct": loss, "output": output}


# ════════════════════════════════════════════════════════════════════════════
# TOOL 5 — get_ospf_neighbors (READ)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def get_ospf_neighbors() -> dict:
    """Show OSPF neighbor adjacencies."""
    output = _vtysh("show ip ospf neighbor")
    return {"output": output}


# ════════════════════════════════════════════════════════════════════════════
# TOOL 6 — configure_interface (WRITE ✏️)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def configure_interface(
    interface: str,
    ip_address: str = "",
    prefix_length: int = 0,
    description: str = "",
    shutdown: bool | None = None,
) -> dict:
    """
    Configure a router interface.

    Args:
        interface:     Interface name e.g. 'eth0', 'lo'.
        ip_address:    IPv4 address to assign (requires prefix_length).
        prefix_length: CIDR prefix length e.g. 24 (requires ip_address).
        description:   Interface description.
        shutdown:      True to shut down, False to bring up, None to leave unchanged.
    """
    _validate_interface(interface)

    has_ip = bool(ip_address or prefix_length)
    has_desc = bool(description)
    has_shut = shutdown is not None

    if not (has_ip or has_desc or has_shut):
        raise ValueError("Provide at least one of: ip_address+prefix_length, description, or shutdown")

    if bool(ip_address) != bool(prefix_length):
        raise ValueError("ip_address and prefix_length must be provided together")

    if ip_address:
        _validate_ipv4(ip_address)
    if prefix_length:
        _validate_prefix_len(prefix_length)
    if description:
        _validate_description(description)

    commands = [f"interface {interface}"]
    if description:
        commands.append(f"description {description}")
    if ip_address and prefix_length:
        commands.append(f"ip address {ip_address}/{prefix_length}")
    if shutdown is True:
        commands.append("shutdown")
    elif shutdown is False:
        commands.append("no shutdown")

    output = _vtysh_config(commands)
    return {
        "interface": interface,
        "applied": {
            "ip_address": f"{ip_address}/{prefix_length}" if ip_address else None,
            "description": description or None,
            "shutdown": shutdown,
        },
        "output": output,
    }


# ════════════════════════════════════════════════════════════════════════════
# TOOL 7 — add_static_route (WRITE ✏️ — bonus)
# ════════════════════════════════════════════════════════════════════════════
@mcp.tool()
def add_static_route(
    prefix: str,
    prefix_length: int,
    next_hop: str,
) -> dict:
    """
    Add a static route.

    Args:
        prefix:        Destination network e.g. '10.0.0.0'.
        prefix_length: CIDR prefix length e.g. 24.
        next_hop:      Next hop IPv4 address.
    """
    _validate_ipv4(prefix)
    _validate_prefix_len(prefix_length)
    _validate_ipv4(next_hop)

    output = _vtysh_config([f"ip route {prefix}/{prefix_length} {next_hop}"])
    return {
        "route": f"{prefix}/{prefix_length} via {next_hop}",
        "output": output,
    }


if __name__ == "__main__":
    mcp.run()
