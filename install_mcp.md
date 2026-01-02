# Installing the FPL MCP Server

## Method 1: Claude Desktop Configuration

1. **Find your Claude Desktop config file location:**
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

2. **Edit the configuration file** and add the FPL server:

```json
{
  "mcpServers": {
    "fpl-server": {
      "command": "python3",
      "args": ["/Users/david/code/fpl-mcp/fpl_server.py"],
      "cwd": "/Users/david/code/fpl-mcp"
    }
  }
}
```

3. **Update the path** to match your actual project location (replace `/Users/david/code/fpl-mcp` with your path)

4. **Restart Claude Desktop** for changes to take effect

## Method 2: Copy Configuration

You can also copy the provided `claude_desktop_config.json` file to your Claude Desktop config location:

```bash
# macOS
cp claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json

# Make sure to edit the paths in the copied file to match your setup
```

## Verification

After installation, you should see the FPL server available in Claude Desktop with the tool:

- **download_latest_data**: Downloads the latest Fantasy Premier League data to CSV files

## Usage Example

Once installed, you can use the tool like this:

```
Please download the latest FPL data to a folder called "current_season"
```

This will call the `download_latest_data` tool with `data_dir` set to "current_season".

## Requirements

Make sure you have the required Python packages installed:

```bash
pip3 install aiohttp pandas
```

## Troubleshooting

- Ensure Python 3.9+ is installed and `python3` command works
- Check that all file paths in the config are absolute and correct
- Restart Claude Desktop after making config changes
- Check Claude Desktop logs if the server doesn't appear