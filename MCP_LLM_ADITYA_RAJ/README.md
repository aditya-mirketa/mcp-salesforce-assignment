# Getting Started

## Setup Python Dependencies
1. Ensure your python environment has - dotenv, fastmcp, httpx

## Setup .env
1. Authenticate your salesforce org using sf-cli
2. Get instanceUrl and accessToken using following command - `sf org display -o <ORG_ALIAS> --json`
3. Add/Update .env in root dir with SALESFORCE_INSTANCE_URL & SALESFORCE_ACCESS_TOKEN

## Setup codex
1. Add MCP server to codex using following command (if you are using uv) - 
`
codex mcp add salesforce-demo -- 
    uv \
    --directory <FULL_PATH\mcp-salesforce-assignment/MCP_LLM_ADITYA_RAJ> \
    run \
    main.py
`
2. If not using uv, update your command as follows - 
codex mcp add <server-name> -- <stdio server-command>

## Usage
1. Goto codex app or cli & interact with MCP server