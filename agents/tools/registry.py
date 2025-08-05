# Tool registry
# agents/tools/registry.py

from .google import google_search_tool
from .calc import calc_tool

# Daftarkan semua tool di sini, key = nama tool yang dipakai di config.tools
TOOL_REGISTRY = {
    "google": google_search_tool,
    "calc": calc_tool,
}

def get_tools_by_names(names: list[str]):
    """
    Kembalikan daftar tool instance sesuai daftar nama.
    Abaikan nama yang tidak dikenal.
    """
    tools = []
    for name in names:
        tool = TOOL_REGISTRY.get(name)
        if tool:
            tools.append(tool)
        else:
            # optional: log atau raise error kalau nama tool tidak ada
            print(f"[WARNING] Tool '{name}' tidak ditemukan di registry")
    return tools
