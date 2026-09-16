# What an owner-approved product-source export may contain

This target exists to do real Local AI Second Brain feature work without touching production. The
export that seeds it is an **owner action**, and this contract states what it may and may not carry.

## May contain

- product **source code**: the Second Brain scripts, the Ask My Brain retrieval code, the web app —
  the logic under development;
- **schema definitions** (DDL, migrations, index definitions) with no rows;
- configuration **templates** with every value replaced by a placeholder;
- documentation describing intended behaviour.

## Must not contain

- any **database contents**: `/AI/Databases/Ask My Brain` and every other store, in whole or in part;
- any **vault** file, or anything derived from `/AI/Knowledge/Second Brain`;
- any real **capture, transcript, inbox item, note, email, calendar entry or voice recording**;
- any **credential**: token, password, API key, SSH key, cookie, session, connection string;
- any **log** containing real content or real addresses;
- any **remote URL** or host entry that names `home`, `192.168.1.100`, `theadmin@`, or production;
- anything the owner has not looked at.

## How it is checked before it is used

The export is unpacked here, then, before the first card runs:

1. `git remote -v` is empty;
2. no file matches a credential shape or names a forbidden host;
3. `fixtures/` contains only synthetic content the owner has approved;
4. the target is authorized with `authorize-target --owner-decision`, which refuses any target whose
   git directory or remote reaches production.

Until an export exists, this repository holds only its scaffold, and no product card can be drafted
against source that is not here.
