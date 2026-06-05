"""A tiny stdio MCP server for testing PATROAM's MCP client. Exposes one tool."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mock-ads")


@mcp.tool()
def get_ad_stats(account: str = "default") -> str:
    """Get performance stats for the latest ad in an account."""
    return ("Latest ad 'Engagement - Copy 4': spend 1,461,359 VND, 19,304 "
            "impressions, 629 clicks, CTR 3.26%, CPC 2,323 VND.")


if __name__ == "__main__":
    mcp.run()
