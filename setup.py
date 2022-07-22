from setuptools import setup

setup(
    name = "zotero2papis",
    version = "1.0",
    description = "A tool to help convert the information from a Zotero SQLite to a folder structure compatible with papis",
    author = "Nicolas Shu",
    author_email = "nicolas.s.shu@gmail.com",
    packages = ["zotero2papis"],
    entry_points = {
            "console_scripts": ["zotero2papis=zotero2papis:run"]
        }
)
