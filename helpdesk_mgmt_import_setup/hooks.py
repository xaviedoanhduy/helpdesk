# Copyright 2025 Kencove (https://www.kencove.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)

import logging

from openupgradelib import openupgrade
from psycopg2.sql import SQL, Identifier

from odoo import SUPERUSER_ID, api
from odoo.tools import sql, table_exists

_logger = logging.getLogger(__name__)

TABLE_TO_CLONES = [
    ("helpdesk_ticket", "ee_helpdesk_ticket"),
    ("helpdesk_tag", "ee_helpdesk_tag"),
    ("helpdesk_ticket_type", "ee_helpdesk_ticket_type"),
    ("helpdesk_sla", "ee_helpdesk_sla"),
    ("helpdesk_sla_status", "ee_helpdesk_sla_status"),
    ("helpdesk_team", "ee_helpdesk_team"),
    ("helpdesk_stage", "ee_helpdesk_stage"),
    ("helpdesk_tag_helpdesk_ticket_rel", "ee_helpdesk_tag_helpdesk_ticket_rel"),
    ("helpdesk_team_res_users_rel", "ee_helpdesk_team_res_users_rel"),
    ("team_stage_rel", "ee_team_stage_rel"),
    ("helpdesk_sla_helpdesk_stage_rel", "ee_helpdesk_sla_helpdesk_stage_rel"),
    ("helpdesk_sla_helpdesk_tag_rel", "ee_helpdesk_sla_helpdesk_tag_rel"),
    (
        "helpdesk_sla_helpdesk_ticket_type_rel",
        "ee_helpdesk_sla_helpdesk_ticket_type_rel",
    ),
    ("helpdesk_sla_res_partner_rel", "ee_helpdesk_sla_res_partner_rel"),
    ("mail_alias", "ee_mail_alias"),
]

MODULE_TO_UNINSTALL = ["helpdesk"]


def _clone_table(cr, old_name, new_name):
    if not table_exists(cr, old_name):
        _logger.warning("Table %s does not exist, skipping.", old_name)
        return
    _logger.info("Cloning table %s to %s...", old_name, new_name)
    cr.execute(SQL("DROP TABLE IF EXISTS {} CASCADE").format(Identifier(new_name)))
    cr.execute(
        SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL)").format(
            Identifier(new_name), Identifier(old_name)
        )
    )
    cr.execute(
        SQL("INSERT INTO {} SELECT * FROM {}").format(
            Identifier(new_name), Identifier(old_name)
        )
    )


def _uninstall_modules(env):
    helpdesk_ee_modules = env["ir.module.module"].search(
        [
            ("name", "in", MODULE_TO_UNINSTALL),
            ("state", "=", "installed"),
        ]
    )
    if helpdesk_ee_modules:
        _logger.info("Triggering uninstall for: %s", MODULE_TO_UNINSTALL)
        helpdesk_ee_modules.sudo().button_uninstall()
    else:
        _logger.info("No EE 'helpdesk' modules currently installed.")


def _ensure_mail_tracking_index(cr):
    if not table_exists(cr, "mail_tracking_email"):
        _logger.warning("Table %s does not exist, skipping.", "mail_tracking_email")
        return

    cr.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE indexname = 'mail_tracking_email_mail_id_index'
    """
    )
    if not cr.fetchone():
        _logger.info("Creating index mail_tracking_email_mail_id_index...")
        cr.execute(
            """
            CREATE INDEX mail_tracking_email_mail_id_index
            ON mail_tracking_email (mail_id)
        """
        )
        _logger.info("Created index mail_tracking_email_mail_id_index.")


def _drop_related_constraint(cr, table_name, constraint_name):
    definition = sql.constraint_definition(cr, table_name, constraint_name)
    if definition:
        _logger.info(
            "Dropping constraint %s on table %s: %s",
            constraint_name,
            table_name,
            definition,
        )
        try:
            sql.drop_constraint(cr, table_name, constraint_name)
        except Exception as e:
            _logger.error(
                "Failed to drop constraint %s on table %s: %s",
                constraint_name,
                table_name,
                str(e),
            )
    else:
        _logger.debug(
            "Constraint %s on table %s not found, skipping.",
            constraint_name,
            table_name,
        )


def _cleanup_helpdesk_references(env, cr):
    if "base.automation" in env:
        rules = (
            env["base.automation"]
            .with_context(active_test=False)
            .search([("model_id.model", "=", "helpdesk.ticket")])
        )
        if rules:
            _logger.info(
                "Deleting %s base.automation rules linked to helpdesk.ticket",
                len(rules),
            )
            rules.unlink()
    else:
        _logger.info("Model base.automation not found in registry, skipping cleanup.")

    if "mail.alias" in env:
        aliases = (
            env["mail.alias"]
            .with_context(active_test=False)
            .search([("alias_model_id.model", "=", "helpdesk.ticket")])
        )
        if aliases:
            _logger.info(
                "Deleting %s mail.alias linked to helpdesk.ticket", len(aliases)
            )
            aliases.unlink()
    else:
        _logger.info("Model mail.alias not found in registry, skipping cleanup.")


def pre_init_hook(cr):
    module = MODULE_TO_UNINSTALL[0]
    if not openupgrade.is_module_installed(cr, module):
        _logger.info("Skipping cloning: module '%s' is not installed.", module)
        return

    for old_name, new_name in TABLE_TO_CLONES:
        try:
            _clone_table(cr, old_name, new_name)
        except Exception as e:
            _logger.error("Failed to clone %s: %s", old_name, str(e))


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    module = MODULE_TO_UNINSTALL[0]
    if not openupgrade.is_module_installed(cr, module):
        _logger.info("Skipping cleanup: module '%s' is not installed.", module)
        return

    try:
        _ensure_mail_tracking_index(cr)

        CONSTRAINTS_TO_DROP = [
            ("helpdesk_team", "helpdesk_team_alias_id_fkey"),
            ("helpdesk_ticket", "helpdesk_ticket_stage_id_fkey"),
        ]
        for table, constraint in CONSTRAINTS_TO_DROP:
            _drop_related_constraint(cr, table, constraint)

        _cleanup_helpdesk_references(env, cr)
        _uninstall_modules(env)
    except Exception as e:
        _logger.error("Error post_init_hook: %s", str(e))
