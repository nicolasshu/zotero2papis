#%%
import yaml
import sqlite3
import os
import glob
import re
import shutil
# %%


class ZoteroSQLParser:
    def __init__(self, zot_dir, output_dir, verbose=True):
        self.zot_dir = zot_dir
        self.out_dir = output_dir
        self.initialize_attributes()
        self.V = verbose

    def initialize_attributes(self):
        self.defaultFile = None
        self.translatedFields = {"DOI": "doi"}
        self.translatedTypes = {"journalArticle": "article"}
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

        pass
    def getTuple(self, elements):
        """
        Concatenate given strings to SQL tule of strings

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
        Query the fields via a SQL connection for an item's ID

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
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
            will create a list of creators, and a single string containing all of the
            creators

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
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
        Query the tags via a SQL connection for an item's ID

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
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
        Query the collections via a SQL connection for an item's ID

        Arguments:
            conn (sqlite3.Connection): An SQL connection
            itemID (int): An integer representing the ID of an
                entry on the Zotero SQL database
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
        # Create a query for the main PDF document
        mimeTypes = self.getTuple(self.includedAttachments.keys())
        attachment_cur = conn.cursor()
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
        attachment_cur.execute(item_attachment_query)

        # Look for the main file associated with the current entry
        files = []
        for attachment_row in attachment_cur:
            key, path, mime, parentID = attachment_row
            if self.V: print(" ")
            if self.V: print("    KEY  ", key)
            if self.V: print("    PATH ", os.path.dirname(path))
            if self.V: print("    PATH ", path)
            if self.V: print("    PARID", parentID)
            dirname = os.path.basename(os.path.dirname(path))
            target_dir = os.path.join(self.out_dir, dirname)
            if self.V: print("    DIR: ", dirname)
            original = os.path.join(self.zot_dir, "storage", key, os.path.basename(path))
            dest = os.path.join(target_dir,os.path.basename(path))
            if self.V: print("      original:", original)
            if self.V: print("      dest:    ", dest)
            files.append(os.path.basename(path))

        if self.V: print("===================================")
        print(f"File: {os.path.basename(path)}")
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
        attachment_cur = conn.cursor()
        attachment_cur.execute(item_attachment_query)
        for attachment_row in attachment_cur:
            if self.V: print("-----------------------")
            key, path, mime, parentID = attachment_row

            if self.V: print("    KEY  ", key)
            if self.V: print("    PATH ", path)
            if self.V: print("    PARID", parentID)

            # If the file is not a storage file, continue onto the next file
            if path[:8] != "storage:": continue

            # Otherwise, copy the file
            filename = path[8:]
            original = os.path.join(self.zot_dir, "storage", key, filename)
            dest = os.path.join(target_dir, filename)
            if self.V: print("      Orig: ", original)
            if self.V: print("      Dest: ", dest)
            files.append(filename)
            if os.path.exists(original) and os.path.exists(dest):
                try:
                    shutil.copyfile(original, dest)
                except:
                    print(f"failed to export attachment {key}: {path} ({mime})")
            else:
                print(f"    {filename}: Already exists")


        if files == [] and defaultFile:
            files.append(defaultFile)
        return {"files": files}, target_dir

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
            if self.V: print(row)
            itemID, itemType, itemKey, dateAdded, dateModified, clientDateModified = row
            itemType = self.translatedTypes.get(itemType, itemType)

            fields = self.getFields(conn, itemID)
            if self.V: print(fields["title"])
            extra = fields.get("extra", None)
            ref = itemKey
            if extra:
                matches = re.search(r'.*Citation Key: (\w+)', extra)
                if matches:
                    ref = matches.group(1)

            item = {
                "ref": ref,
                "type": itemType,
                "created": dateAdded,
                "modified": dateModified,
                "modified.client": clientDateModified
            }
            item.update(fields)
            creators = self.getCreators(conn, itemID)
            item.update(creators)
            tags = self.getTags(conn, itemID)
            item.update(tags)
            collections = self.getCollections(conn, itemID)
            item.update(collections)
            files, target_dir = self.getFiles(conn, itemID, itemKey)
            item.update(files)
            item.update({"ref": ref})

            if not os.path.exists(target_dir):
                os.makedirs(target_dir)

            with open(os.path.join(target_dir, "info.yaml"), "w+") as f:
                yaml.dump(item, f, default_flow_style=False)

            if self.V: print("\n\n") 



# Location of the zotero.sqlite file
# zot_path = "/home/nickshu/Zotero/"
# out_path = "/home/nickshu/experiments/zotero_sql/output2"

import click
@click.command()
@click.option("--zotdir",help="parent directory of Zotero database")
@click.option("--outdir",help="output directory")
def run(zotdir, outdir):
    client = ZoteroSQLParser(zotdir, outdir)
    client.run()

if __name__ == "__main__":
    run()




