# -*- coding: utf-8 -*-
import logging

import psycopg2
from cnxarchive.scripts import export_epub
from pyramid.events import subscriber
from pyramid.threadlocal import get_current_registry


from . import events
from .bake import remove_baked, bake
from .db import (
    set_post_publications_state,
    update_module_state,
    with_db_cursor,
)


logger = logging.getLogger('cnxpublishing')

CONTACT_SITE_ADMIN_MESSAGE = ("A system's error occured, please contact the "
                              "site administrator for assistance.")


@subscriber(events.PostPublicationEvent)
@with_db_cursor
def post_publication_processing(event, cursor):
    """Process post-publication events coming out of the database."""
    module_ident, ident_hash = event.module_ident, event.ident_hash
    logger.debug('Processing module_ident={} ident_hash={}'.format(
        module_ident, ident_hash))
    update_module_state(cursor, module_ident, 'processing')
    set_post_publications_state(cursor, module_ident, 'Processing')
    # Commit the state change before preceding.
    cursor.connection.commit()

    try:
        binder = export_epub.factory(ident_hash)
    except:
        logger.exception('Logging an uncaught exception during baking'
                         'ident_hash={} module_ident={}'
                         .format(ident_hash, module_ident))
        # FIXME If the top module entry doesn't exist, this is going to fail.
        try:
            update_module_state(cursor, module_ident, 'errored')
        except psycopg2.Error:  # pragma: no cover
            pass
        state_message = CONTACT_SITE_ADMIN_MESSAGE
        set_post_publications_state(cursor, module_ident, 'Failed/Error',
                                    state_message=state_message)
        return
    finally:
        logger.debug('Finished exporting module_ident={} ident_hash={}'
                     .format(module_ident, ident_hash))

    cursor.execute("""\
SELECT submitter, submitlog FROM modules
WHERE ident_hash(uuid, major_version, minor_version) = %s""",
                   (ident_hash,))
    publisher, message = cursor.fetchone()
    remove_baked(ident_hash, cursor=cursor)

    state = 'current'
    pub_state = 'Done/Success'
    state_message = None
    try:
        bake(binder, publisher, message, cursor=cursor)
    except Exception as exc:
        state = 'errored'
        pub_state = 'Failed/Error'
        state_message = CONTACT_SITE_ADMIN_MESSAGE
        # TODO rollback to pre-removal of the baked content??
        logger.exception('Logging an uncaught exception during baking')
    finally:
        logger.debug('Finished processing module_ident={} ident_hash={} '
                     'with a final state of \'{}\'.'
                     .format(module_ident, ident_hash, state))
        update_module_state(cursor, module_ident, state)
        set_post_publications_state(cursor, module_ident, pub_state,
                                    state_message=state_message)


@subscriber(events.ChannelProcessingStartUpEvent)
@with_db_cursor
def post_publication_start_up(event, cursor):
    # If you make changes to the payload, be sure to update the trigger
    # code as well.
    cursor.execute("""\
SELECT pg_notify('post_publication',
                 '{"module_ident": '||
                 module_ident||
                 ', "ident_hash": "'||
                 ident_hash(uuid, major_version, minor_version)||
                 '", "timestamp": "'||
                 CURRENT_TIMESTAMP||
                 '"}')
FROM modules
WHERE stateid = (
    SELECT stateid
    FROM modulestates
    WHERE statename = 'post-publication');""")


__all__ = (
    'post_publication_processing',
    'post_publication_start_up',
)