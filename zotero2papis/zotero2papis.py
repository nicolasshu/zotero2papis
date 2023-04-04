#%%
import yaml
import sqlite3
import os
import glob
import re
import shutil
import dateutil.parser
# %%


class ZoteroSQLParser:
    def __init__(self, zot_dir, output_dir, verbose=False):
        # Setup the Zotero config root directory
        self.zot_dir = zot_dir

        # Setup the output directory (Ideally where papis will look for papers)
        self.out_dir = output_dir
        self.initialize_attributes()
        self.V = verbose

    def initialize_attributes(self):
        self.defaultFile = None
        self.translatedFields = {"DOI": "doi"}
        self.translatedTypes = {"journalArticle": "article"}

        # Define the attachments to look for
        self.includedAttachments = {"application/vnd.ms-htmlhelp":  "chm",
                            "image/vnd.djvu": "djvu",
                            "application/msword":  "doc",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
                            "application/epub+zip": "epub",
                            "application/octet-stream":  "fb2",
                            "application/x-mobipocket-ebook": "mobi",
                            "application/pdf":  "pdf",
                            "text/rtf":  "rtf",
                            "application/zip":  "zip"}

        # List of types that we will ignore
        self.excludedTypes = ["note"]
        self.excludedTypes.append("attachment")
        self.excludedTypeTuple = self.getTuple(self.excludedTypes)

    def getTuple(self, elements):
        """
        Concatenate given strings to SQL tuple of strings. E.g.
        Let elements = ['thing1', 'thing2', 'thing3']. The output will be:

            '("thing1","thing2","thing3")'

        Arguments:
            elements: array-like iterable of strings

        Returns:
            A single string in the form of '("item1","item2","item3")'
        """
        elementsTuple = "("
        for element in elements:
            if elementsTuple != "(":
                elementsTuple += ","
            elementsTuple += "\"" + element + "\""
        elementsTuple += ")"
        return elementsTuple

    def getFields(self, conn, itemID):
        """
        Query the fields via a SQL connection for an item's ID, returning a dictionary.
        For example, it will return:
            {'title': 'Title of Article',
             'abstractNote': 'Abstract of the article',
             'date': '2017-12-05 2017-12-05',
             'libraryCatalog': 'arXiv.org',
             'url': 'http://arxiv.org/abs/xxxx.xxxxx',
             'accessDate': '2022-07-22 23:44:23',
             'extra': 'arXiv:xxxx.xxxxx [cs]',
             'doi': '10.48550/arXiv.xxxx.xxxxx',
             'repository': 'arXiv',
             'archiveID': 'arXiv:xxxx.xxxxx'
            }

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
        Returns:
            (dict) Dictionary containing all of the fields of interest
        """
        item_field_query = f"""
        SELECT
            fields.fieldName,
            itemDataValues.value
        FROM
            fields,
            itemData,
            itemDataValues
        WHERE
            itemData.itemID = {itemID} AND
            fields.fieldID = itemData.fieldID AND
            itemDataValues.valueID = itemData.valueID
        """
        field_cur = conn.cursor()
        field_cur.execute(item_field_query)
        fields = {}
        for field_row in field_cur:
            fieldName = self.translatedFields.get(field_row[0], field_row[0])
            fieldValue = field_row[1]
            fields[fieldName] = fieldValue
        return fields

    def getCreators(self, conn, itemID):
        """
        Query the creators of the article via a SQL connection for an item's ID. This
            will create a dict of creators, which contains a list of dictionaries for
            all of the authors, and a single string containing all of the creators.
            For example:

            {'author': 'lastA, firstA and lastB, firstB and lastC, firstC'
             'author_list': [{'given_name': 'firstA', 'surname': 'lastA'},
                             {'given_name': 'firstB', 'surname': 'lastB'},
                             {'given_name': 'firstC', 'surname': 'lastC'}
                            ]
            }

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
        Returns:
            (dict) Dictionary containing information on all of the creators
        """
        item_creator_query = f"""
        SELECT
            creatorTypes.creatorType,
            creators.firstName,
            creators.lastName
        FROM
            creatorTypes,
            creators,
            itemCreators
        WHERE
            itemCreators.itemID = {itemID} AND
            creatorTypes.creatorTypeID = itemCreators.creatorTypeID AND
            creators.creatorID = itemCreators.creatorID
        ORDER BY
            creatorTypes.creatorType,
            itemCreators.orderIndex
        """
        creator_cur = conn.cursor()
        creator_cur.execute(item_creator_query)
        creators = {}

        for creator_row in creator_cur:
            # Get the data from a single query
            creatorName, givenName, surname = creator_row
            creatorNameList = creatorName + "_list"

            # Compile the string of creators
            currentCreators = creators.get(creatorName, "")
            if currentCreators != "":
                currentCreators += " and "
            currentCreators += f"{surname}, {givenName}"
            creators[creatorName] = currentCreators

            # Create the list of creators
            currentCreatorsList = creators.get(creatorNameList, [])
            currentCreatorsList.append({"given_name": givenName, "surname": surname})
            creators[creatorNameList] = currentCreatorsList
        return creators

    def getTags(self, conn, itemID):
        """
        Query the tags via a SQL connection for an item's ID. For example:
            {"tags": {"Computer Science - Computation and Language",
                      "Computer Science - Machine Learning"
                     }
            }

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
        Returns:
            (dict) Dictionary containing a set of all of the tags
        """
        tag_delimiter = ","
        item_tag_query = f"""
        SELECT tags.name FROM tags, itemTags
        WHERE
            itemTags.itemID = {itemID} AND
            tags.tagID = itemTags.tagID
        """
        tag_cur = conn.cursor()
        tag_cur.execute(item_tag_query)
        tags = []
        for tag_row in tag_cur:
            if tags != "":
                tags.append(tag_row[0])

        # TODO See if this can take multiple tags, as in a list of tags
        return {"tags": tags}

    def getCollections(self, conn, itemID):
        """
        Query the collections via a SQL connection for an item's ID. E.g.
            {"project": {"collectionA", "collectionB"}}

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
        Returns:
            (dict) Dictionary containing a set of all the collections
                associated with this item
        """
        item_collection_query = f"""
        SELECT
            collections.collectionName
        FROM
            collections, collectionItems
        WHERE
            collectionItems.itemID = {itemID} AND
            collections.collectionID = collectionItems.collectionID
        """
        collection_cur = conn.cursor()
        collection_cur.execute(item_collection_query)
        collections = [c_row[0] for c_row in collection_cur]
        return {"project": collections}

    def getFiles(self, conn, itemID, itemKey):
        """
        Query and copy the files via a SQL connection for an item's ID

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
        """
        # ===================================================================
        # COPY THE PDF FILE
        # ===================================================================
        # Create a query for the main PDF document
        mimeTypes = self.getTuple(self.includedAttachments.keys())
        attachment_cur = conn.cursor()
        has_pdf = False
        item_attachment_query = f"""
        SELECT
            items.key,
            itemAttachments.path,
            itemAttachments.contentType,
            itemAttachments.parentItemID
        FROM
            itemAttachments, items
        WHERE
            itemAttachments.parentItemID = {itemID} AND
            itemAttachments.contentType IN {mimeTypes} AND
            items.itemID = itemAttachments.itemID
        """
        # The last line "items.itemID = itemAttachments.itemID" ensures it
        # it only returns the main attachment (i.e. PDF)

        # Perform the SQL query
        attachment_cur.execute(item_attachment_query)
        # attachment_cur now is a sqlite3.Cursor object

        # Look for the main file associated with the current entry
        files = []
        for attachment_row in attachment_cur:
            has_pdf = True
            # Obtain the item information from the cursor
            key, path, mime, parentID = attachment_row
            # Information:
            #    key:      (str) Zotero unique key identifier (e.g. 'ABCDEF1G')
            #    mime:     (str) Filetype (e.g. 'application/pdf')
            #    path:     (str) Path to the file location
            #    parentID: (int) ID of the parent
            # if self.V: print(" ")
            if self.V: print("    KEY  ", key)
            if self.V: print("    PATH ", path)
            if self.V: print("    PARID", parentID)

            if path[:8] == "storage:":
                # This occurs when you add the file straight from the PDF file
                # on the Zotero connector. Otherwise, you will not have the
                # 'storage:' string in the `path` variable
                path = os.path.join(self.zot_dir, "storage", key, path[8:])

                # Create a new directory name in the format of YYYY_title
                try:
                    # Try to guess any date config (e.g. YYYYMMDD, DD-MM-YYYY)
                    date = dateutil.parser.parse(self.item["date"])
                except:
                    date = dateutil.parser.parse(self.item["date"][:-3])
                dirname = os.path.join(f"{date.year}_{self.item['title']}")
            else:
                # Directory name of the paper (e.g. title of the paper). E.g. 
                #    'Attention Is All You Need'
                #
                # You need to keep this line because if you are using Zotfile,
                # and an article's title is too long, the directory will be
                # cropped. So this will need to know where the PDF file was
                # created. If you don't use Zotfile, it will always use the
                # case above and it will save it as `papis/YYYY_title/...pdf`
                dirname = os.path.basename(os.path.dirname(path))

            # Directory destination where file will be copied. E.g.
            #    './papers/Attention Is All You Need'
            target_dir = os.path.join(self.out_dir, dirname)
            # if not self.V: print(f"{dirname}")
            if self.V: print("    DIR: ", dirname)

            # Compile the location where the file is supposed to be
            # stored by Zotero. E.g.
            #    './zotero/storage/{ABCDEF1G}/{author_year_title}.pdf
            #
            # The filename is defined in
            #   Tools > ZotFile Preferences > Renaming Rules > Format for all Item Types except Patents
            original = os.path.join(self.zot_dir, "storage", key, os.path.basename(path))

            # Destination where file will be stored. E.g.
            #    './papers/Attention Is All You Need/{author_year_title}.pdf
            dest = os.path.join(target_dir,os.path.basename(path))
            files.append(os.path.basename(path))
            if self.V: print("      original:", original)
            if self.V: print("      dest:    ", dest)
            if os.path.exists(dest) and not os.path.exists(original):
                # If the file exists at the destination and does not exist in the original
                # location, just mention it
                print("    File in the path below has already been moved")
                print(f"       {original}")
            elif path != dest:
                # If the current location of the file is not where it should be moved to,
                # then make the directories necessary, and copy the file there.
                # TODO: Investigate this a little more
                if not os.path.exists(path):
                    print(f"    The file in the path below does not exist\n      {path}")
                    if not os.path.exists(os.path.dirname(path)):
                        # This entry has been deleted
                        return None, None, True
                    continue
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                shutil.copyfile(path, dest)
                print(f"  File has been copied to {dest}")


        # -------------------------------------------------------------------
        # COPY ALL OTHER FILES
        # -------------------------------------------------------------------
        print(f"    Now parsing other storage files...")

        # Look for other files associated with the current parent entry
        item_attachment_query = f"""
        SELECT
            items.key,
            itemAttachments.path,
            itemAttachments.contentType,
            itemAttachments.parentItemID
        FROM
            itemAttachments, items
        WHERE
            itemAttachments.parentItemID = {itemID} AND
            items.itemID = itemAttachments.itemID
        """
        # This query will look for all of the files associated with the entry.
        # It will also include the PDF

        # Perform the query
        attachment_cur = conn.cursor()
        attachment_cur.execute(item_attachment_query)
        for attachment_row in attachment_cur:
            if self.V: print("-----------------------")
            key, path, mime, parentID = attachment_row

            if self.V: print("    KEY  ", key)
            if self.V: print("    PATH ", path)
            if self.V: print("    PARID", parentID)

            # If the file is not a storage file, continue onto the next file
            # Ignore the PDF file
            if path[:8] != "storage:": continue
            print(f"      Storage File: {path[8:]}")

            # Check to see if the file exists
            # if not os.path.exists(path):
            #     print(f"\tThe file in path {path} does not exist. Skipping it.")
            #     continue

            # Otherwise, copy the file
            filename = path[8:]
            original = os.path.join(self.zot_dir, "storage", key, filename)
            if has_pdf:
                dest = os.path.join(target_dir, filename)
            else:
                try:
                    date = dateutil.parser.parse(self.item["date"])
                except:
                    date = dateutil.parser.parse(self.item["date"][:-3])
                dirname = os.path.join(f"{date.year}_{self.item['title']}")
                target_dir = os.path.join(self.out_dir, dirname)
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                dest = os.path.join(target_dir, filename)
            if self.V: print("      :Orig: ", original)
            if self.V: print("      :Dest: ", dest)
            files.append(filename)
            if os.path.exists(dest):
                print(f"       - Already exists")
            else:
                if os.path.exists(original):
                    try:
                        if os.path.exists(os.path.dirname(dest)):
                            shutil.copyfile(original, dest)
                            print(f"       - Copied")
                        else:
                            print(f"       - Directory does not exist: {os.path.dirname(dest)}")
                    except:
                        print(f"Failed to export attachment {key}: {path} ({mime})")
                else:
                    print(f"The original file {original} does not exist")

        if files == [] and defaultFile:
            files.append(defaultFile)
        return {"files": files}, target_dir, False

    def get_number_of_entries(self):
        conn = sqlite3.connect(os.path.join(self.zot_dir,"zotero.sqlite"))
        cur  = conn.cursor()

        # Query the number of entries to go through
        items_count_query = f"""
        SELECT
            COUNT(item.itemID)
        FROM
            items item,
            itemTypes itemType
        WHERE
            itemType.itemTypeID = item.itemTypeID AND
            itemType.typeName NOT IN {self.excludedTypeTuple}
        ORDER BY
            item.itemID
        """
        cur.execute(items_count_query)
        for row in cur:
            itemsCount = row[0]
        return itemsCount

    def run(self):
        conn = sqlite3.connect(os.path.join(self.zot_dir,"zotero.sqlite"))
        cur  = conn.cursor()

        # Query the entries to go through
        items_query = f"""
        SELECT
            item.itemID, itemType.typeName, key,
            dateAdded, dateModified, clientDateModified
        FROM
            items item,
            itemTypes itemType
        WHERE
            itemType.itemTypeID = item.itemTypeID AND
            itemType.typeName NOT IN {self.excludedTypeTuple}
        ORDER BY
            item.itemID
        """

        cur.execute(items_query)
        for row in cur:
            itemID, itemType, itemKey, dateAdded, dateModified, clientDateModified = row
            itemType = self.translatedTypes.get(itemType, itemType)

            # Get the field (e.g. title, abstract, date) associated with this item
            fields = self.getFields(conn, itemID)
            if ("date" in fields):
                if len(fields["date"].split(" ")) > 1:
                    fields["date"] = fields["date"].split(" ")[0]

            if self.V: print("===================================")
            if self.V: print(f'  TITLE: {fields["title"]}')
            if not self.V: print(f"{fields['title']}")
            extra = fields.get("extra", None)
            ref = itemKey
            if extra:
                matches = re.search(r'.*Citation Key: (\w+)', extra)
                if matches:
                    ref = matches.group(1)

            self.item = {
                "ref": ref,
                "type": itemType,
                "created": dateAdded,
                "modified": dateModified,
                "modified.client": clientDateModified
            }

            # Place the fields in the dictionary
            self.item.update(fields)

            # Obtain all the authors and put them in the item dict
            creators = self.getCreators(conn, itemID)
            if self.V: print(f'AUTHORS: {creators["author"]}')
            if self.V: print("===================================")
            self.item.update(creators)

            # Obtain all the tags and put them in the item dict
            tags = self.getTags(conn, itemID)
            self.item.update(tags)

            # Obtain all the collections and put them in the item dict
            collections = self.getCollections(conn, itemID)
            self.item.update(collections)

            # 
            files, target_dir, deleted_entry = self.getFiles(conn, itemID, itemKey)
            if deleted_entry:
                print(f"      ENTRY HAS BEEN DELETED VIA ZOTERO")
                continue
            self.item.update(files)

            skip = True
            for f in files["files"]:
                # Avoid entries that were deleted via Zotero
                if not os.path.exists(target_dir) and os.path.exists(os.path.join(target_dir,f)):
                    os.makedirs(target_dir)
                    skip = False
                    break

            # if not os.path.exists(os.path.join(target_dir, "info.yaml")):
            # if not skip:
            if os.path.exists(target_dir) and not os.path.exists(os.path.join(target_dir, "info.yaml")):
                with open(os.path.join(target_dir, "info.yaml"), "w+") as f:
                    yaml.dump(self.item, f, default_flow_style=False)

            if self.V: print("\n\n") 



# Location of the zotero.sqlite file
# zot_path = "/home/nickshu/Zotero/"
# out_path = "/home/nickshu/experiments/zotero_sql/output2"

import click
@click.command()
@click.option("--zotdir", "-z", help="parent directory of Zotero database")
@click.option("--outdir", "-o", help="output directory")
@click.option("--verbose", "-v", is_flag = True, help="whether to show verbose logs")
@click.help_option("-h")
def run(zotdir, outdir, verbose):
    client = ZoteroSQLParser(zot_dir=zotdir, output_dir=outdir, verbose=verbose)
    client.run()

if __name__ == "__main__":
    run()

