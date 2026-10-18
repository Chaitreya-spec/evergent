"""Tool names, grouped. Kept import-free so agents can name tools cheaply."""

# Every worker can see and edit its workspace; these are not optional.
FILE_TOOLS = ["list_files", "read_file", "write_file"]
WEB_TOOLS = ["web_search", "fetch_url"]
CODE_TOOLS = ["run_code"]
DATABASE_TOOLS = ["run_sql"]

ALL_TOOLS = [*FILE_TOOLS, *WEB_TOOLS, *CODE_TOOLS, *DATABASE_TOOLS]
OPTIONAL_TOOLS = [*WEB_TOOLS, *CODE_TOOLS, *DATABASE_TOOLS]
