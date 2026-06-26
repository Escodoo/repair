# Copyright 2026 - TODAY, Kaynnan Lemes <kaynnan.lemes@escodoo.com.br>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command
from odoo.tests.common import TransactionCase


class TestRepairAnalyticDistribution(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.analytic_plan = cls.env["account.analytic.plan"].create(
            {"name": "Test Repair Plan"}
        )
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {"name": "Test Repair Account", "plan_id": cls.analytic_plan.id}
        )
        cls.analytic_distribution = {str(cls.analytic_account.id): 100.0}

        cls.product_to_repair = cls.env["product.product"].create(
            {"name": "Product To Repair", "type": "consu"}
        )
        cls.product_add = cls.env["product.product"].create(
            {
                "name": "Part Add",
                "type": "consu",
                "list_price": 50.0,
                "standard_price": 20.0,
            }
        )
        cls.product_remove = cls.env["product.product"].create(
            {
                "name": "Part Remove",
                "type": "consu",
                "list_price": 30.0,
                "standard_price": 10.0,
            }
        )
        cls.product_recycle = cls.env["product.product"].create(
            {
                "name": "Part Recycle",
                "type": "consu",
                "list_price": 15.0,
                "standard_price": 5.0,
            }
        )

        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.repair_type = cls.warehouse.repair_type_id

    def _make_repair(self, analytic_distribution=None, price_type="cost"):
        self.repair_type.write({"analytic_price_type": price_type})
        return self.env["repair.order"].create(
            {
                "product_id": self.product_to_repair.id,
                "picking_type_id": self.repair_type.id,
                "analytic_distribution": (
                    analytic_distribution
                    if analytic_distribution is not None
                    else self.analytic_distribution
                ),
                "move_ids": [
                    Command.create(
                        {
                            "product_id": self.product_add.id,
                            "product_uom_qty": 2.0,
                            "repair_line_type": "add",
                            "company_id": self.env.company.id,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product_remove.id,
                            "product_uom_qty": 3.0,
                            "repair_line_type": "remove",
                            "company_id": self.env.company.id,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product_recycle.id,
                            "product_uom_qty": 1.0,
                            "repair_line_type": "recycle",
                            "company_id": self.env.company.id,
                        }
                    ),
                ],
            }
        )

    def _complete_repair(self, repair):
        repair._action_repair_confirm()
        repair.action_repair_start()
        for move in repair.move_ids:
            move.quantity = move.product_uom_qty
        repair.action_repair_end()

    def _analytic_lines(self, repair):
        return self.env["account.analytic.line"].search(
            [("repair_order_id", "=", repair.id)]
        )

    def test_analytic_lines_created_on_done(self):
        """Completing a repair creates one analytic line per part move."""
        repair = self._make_repair()
        self._complete_repair(repair)

        lines = self._analytic_lines(repair)
        self.assertEqual(
            len(lines),
            3,
            "Expected one analytic line per part move (add, remove, recycle).",
        )

    def test_no_analytic_lines_without_distribution(self):
        """No analytic lines are created when analytic_distribution is empty."""
        repair = self._make_repair(analytic_distribution=False)
        self._complete_repair(repair)

        self.assertFalse(
            self._analytic_lines(repair),
            "No analytic lines expected when distribution is not set.",
        )

    def test_analytic_lines_sign_per_move_type(self):
        """add → negative; remove and recycle → positive."""
        repair = self._make_repair()
        self._complete_repair(repair)

        cases = [
            ("add", self.product_add, -1),
            ("remove", self.product_remove, 1),
            ("recycle", self.product_recycle, 1),
        ]
        for line_type, product, expected_sign in cases:
            with self.subTest(repair_line_type=line_type):
                line = self._analytic_lines(repair).filtered(
                    lambda al, p=product: al.product_id == p
                )
                self.assertEqual(len(line), 1)
                self.assertEqual(
                    (1 if line.amount > 0 else -1),
                    expected_sign,
                    f"Wrong sign for repair_line_type='{line_type}'.",
                )

    def test_analytic_amount_uses_standard_price(self):
        """With price_type='cost', amount = standard_price × quantity × sign."""
        repair = self._make_repair(price_type="cost")
        self._complete_repair(repair)

        cases = [
            (self.product_add, "add", -1),
            (self.product_remove, "remove", 1),
            (self.product_recycle, "recycle", 1),
        ]
        for product, line_type, sign in cases:
            with self.subTest(repair_line_type=line_type):
                move = repair.move_ids.filtered(lambda m, p=product: m.product_id == p)
                line = self._analytic_lines(repair).filtered(
                    lambda al, p=product: al.product_id == p
                )
                expected = sign * product.standard_price * move.quantity
                self.assertAlmostEqual(
                    line.amount,
                    expected,
                    places=2,
                    msg=(
                        f"Wrong amount for repair_line_type='{line_type}'"
                        " with cost type."
                    ),
                )

    def test_analytic_amount_uses_price_unit(self):
        """With price_type='price' and no SO, amount = move.price_unit × qty × sign."""
        repair = self._make_repair(price_type="price")
        # Without a linked sale order, price_unit must be set explicitly on the move.
        add_move = repair.move_ids.filtered(lambda m: m.repair_line_type == "add")
        add_move.price_unit = 50.0

        repair._action_repair_confirm()
        repair.action_repair_start()
        for move in repair.move_ids:
            move.quantity = move.product_uom_qty
        repair.action_repair_end()

        line = self._analytic_lines(repair).filtered(
            lambda al: al.product_id == self.product_add
        )
        expected = -1 * 50.0 * add_move.quantity
        self.assertAlmostEqual(line.amount, expected, places=2)

    def test_analytic_amount_uses_sale_line_price_unit(self):
        """With price_type='price' and a linked SO line, amount uses SO line price."""
        repair = self._make_repair(price_type="price")
        add_move = repair.move_ids.filtered(lambda m: m.repair_line_type == "add")

        partner = self.env["res.partner"].search([], limit=1)
        sale_order = self.env["sale.order"].create({"partner_id": partner.id})
        sol = self.env["sale.order.line"].create(
            {
                "order_id": sale_order.id,
                "product_id": self.product_add.id,
                "product_uom_qty": 2.0,
                "price_unit": 70.0,
            }
        )
        add_move.sale_line_id = sol

        repair._action_repair_confirm()
        repair.action_repair_start()
        for move in repair.move_ids:
            move.quantity = move.product_uom_qty
        repair.action_repair_end()

        line = self._analytic_lines(repair).filtered(
            lambda al: al.product_id == self.product_add
        )
        expected = -1 * 70.0 * add_move.quantity
        self.assertAlmostEqual(line.amount, expected, places=2)

    def test_analytic_amount_distribution_percentage(self):
        """With 60% distribution on one account, amount is proportional."""
        second_account = self.env["account.analytic.account"].create(
            {"name": "Second Account", "plan_id": self.analytic_plan.id}
        )
        distribution = {
            str(self.analytic_account.id): 60.0,
            str(second_account.id): 40.0,
        }
        repair = self._make_repair(
            analytic_distribution=distribution, price_type="cost"
        )
        self._complete_repair(repair)

        lines = self._analytic_lines(repair)
        # 3 moves × 2 accounts = 6 lines
        self.assertEqual(len(lines), 6)

        add_move = repair.move_ids.filtered(lambda m: m.repair_line_type == "add")
        total_expected = -1 * self.product_add.standard_price * add_move.quantity

        add_lines = lines.filtered(lambda al: al.product_id == self.product_add)
        with self.subTest(account="primary_60pct"):
            primary = add_lines.filtered(
                lambda al: al[self.analytic_plan._column_name()]
                == self.analytic_account
            )
            self.assertAlmostEqual(primary.amount, total_expected * 0.6, places=2)

        with self.subTest(account="secondary_40pct"):
            secondary = add_lines.filtered(
                lambda al: al[self.analytic_plan._column_name()] == second_account
            )
            self.assertAlmostEqual(secondary.amount, total_expected * 0.4, places=2)

    def test_analytic_lines_deleted_on_cancel(self):
        """Cancelling a repair deletes its linked analytic lines."""
        repair = self._make_repair()
        self.env["account.analytic.line"].sudo().create(
            [
                {
                    "name": repair.name,
                    "repair_order_id": repair.id,
                    "company_id": repair.company_id.id,
                    self.analytic_plan._column_name(): self.analytic_account.id,
                },
                {
                    "name": repair.name,
                    "repair_order_id": repair.id,
                    "company_id": repair.company_id.id,
                    self.analytic_plan._column_name(): self.analytic_account.id,
                },
            ]
        )
        self.assertEqual(len(self._analytic_lines(repair)), 2)

        repair.action_repair_cancel()

        self.assertFalse(
            self._analytic_lines(repair),
            "Analytic lines must be deleted when repair is cancelled.",
        )

    def test_analytic_lines_deleted_on_unlink(self):
        """Deleting a repair order also removes its analytic lines."""
        repair = self._make_repair()
        self.env["account.analytic.line"].sudo().create(
            {
                "name": repair.name,
                "repair_order_id": repair.id,
                "company_id": repair.company_id.id,
                self.analytic_plan._column_name(): self.analytic_account.id,
            }
        )
        repair_id = repair.id
        self.assertEqual(
            self.env["account.analytic.line"].search_count(
                [("repair_order_id", "=", repair_id)]
            ),
            1,
        )

        repair.unlink()

        self.assertEqual(
            self.env["account.analytic.line"].search_count(
                [("repair_order_id", "=", repair_id)]
            ),
            0,
            "Analytic lines must be deleted when the repair order is removed.",
        )

    def test_analytic_line_count(self):
        """analytic_line_count reflects the number of linked analytic lines."""
        repair = self._make_repair()

        self.assertEqual(repair.analytic_line_count, 0)

        self._complete_repair(repair)

        self.assertEqual(repair.analytic_line_count, 3)

    def test_action_view_analytic_lines_returns_correct_domain(self):
        """Smart button action filters lines by the current repair."""
        repair = self._make_repair()
        self._complete_repair(repair)

        action = repair.action_view_analytic_lines()

        self.assertEqual(action["res_model"], "account.analytic.line")
        self.assertIn(("repair_order_id", "=", repair.id), action["domain"])

    def test_analytic_plan_business_domain_includes_repair(self):
        """'repair' is available as a business domain in analytic applicability."""
        applicability = self.env["account.analytic.applicability"].create(
            {
                "business_domain": "repair",
                "applicability": "optional",
                "analytic_plan_id": self.analytic_plan.id,
            }
        )
        self.assertEqual(applicability.business_domain, "repair")

    def test_analytic_picking_type_price_type_field(self):
        """analytic_price_type is selectable on a repair operation type."""
        for price_type in ("price", "cost"):
            with self.subTest(price_type=price_type):
                self.repair_type.write({"analytic_price_type": price_type})
                self.assertEqual(self.repair_type.analytic_price_type, price_type)
