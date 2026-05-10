from app.path_utils import (
    ancestor_dir_prefixes_for_file,
    basename,
    depth,
    normalize_virtual_path,
    parent_path,
    path_prefixes,
)


def test_normalize_virtual_path():
    assert normalize_virtual_path("/docs/./auth/../api") == "/docs/api"
    assert normalize_virtual_path("../x", "/docs/auth") == "/docs/x"
    assert normalize_virtual_path("../../x", "/docs/auth") == "/x"
    assert normalize_virtual_path("../../../x", "/docs/auth") == "/x"
    assert normalize_virtual_path(".", "/docs") == "/docs"


def test_path_parts():
    assert parent_path("/docs/auth/oauth.mdx") == "/docs/auth"
    assert basename("/docs/auth/oauth.mdx") == "oauth.mdx"
    assert depth("/") == 0
    assert depth("/docs/auth/oauth.mdx") == 3


def test_prefixes():
    assert path_prefixes("/") == ["/"]
    assert path_prefixes("/docs/auth") == ["/", "/docs", "/docs/auth"]
    assert ancestor_dir_prefixes_for_file("/docs/auth/oauth.mdx") == ["/", "/docs", "/docs/auth"]
