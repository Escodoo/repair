# Copyright 2026 - TODAY, Cristiano Mafra Junior <cristiano.mafra@escodoo.com.br>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    repair_timesheet_default_project_id = fields.Many2one(
        related="company_id.repair_timesheet_default_project_id",
        domain=[("allow_timesheets", "=", True)],
        readonly=False,
    )
    repair_timesheet_default_task_id = fields.Many2one(
        related="company_id.repair_timesheet_default_task_id",
        domain="[('project_id', '=', repair_timesheet_default_project_id)]",
        readonly=False,
    )
