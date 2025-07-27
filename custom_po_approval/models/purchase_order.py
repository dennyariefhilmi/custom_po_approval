from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, AccessError
import logging

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    approval_state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted for Approval'),
        ('pending_manager', 'Pending Manager Approval'),
        ('pending_dept_head', 'Pending Department Head Approval'),
        ('pending_cfo', 'Pending CFO Approval'),
        ('pending_cfo_direct', 'Pending CFO Approval (Direct)'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Approval State', default='draft', tracking=True)

    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True)
    approved_date = fields.Datetime(string='Approved Date', readonly=True)
    rejected_by = fields.Many2one('res.users', string='Rejected By', readonly=True)
    rejected_date = fields.Datetime(string='Rejected Date', readonly=True)
    rejection_reason = fields.Text(string='Rejection Reason')

    current_approver = fields.Many2one('res.users', string='Current Approver', readonly=True)
    approval_sequence = fields.Text(string='Approval Sequence History', readonly=True)

    @api.depends('order_line.price_total')
    def _compute_amount_total_company_currency(self):
        """Compute total amount in company currency for approval logic"""
        for order in self:
            if order.currency_id != order.company_id.currency_id:
                order.amount_total_company = order.currency_id._convert(
                    order.amount_total,
                    order.company_id.currency_id,
                    order.company_id,
                    order.date_order or fields.Date.today()
                )
            else:
                order.amount_total_company = order.amount_total

    amount_total_company = fields.Monetary(
        compute='_compute_amount_total_company_currency',
        currency_field='company_currency_id',
        string='Total (Company Currency)',
        store=True
    )

    company_currency_id = fields.Many2one(
        related='company_id.currency_id',
        string='Company Currency'
    )

    def _get_approval_route(self):
        """Determine approval route based on amount"""
        self.ensure_one()
        amount_idr = self.amount_total_company

        if amount_idr < 5000000:  # Less than IDR 5 million
            return 'manager'
        elif amount_idr <= 20000000:  # Between IDR 5-20 million
            return 'dept_head_cfo'
        else:  # More than IDR 20 million
            return 'cfo_direct'

    def action_submit_for_approval(self):
        """Submit PO for approval"""
        for order in self:
            if order.approval_state != 'draft':
                raise ValidationError(_('Only draft purchase orders can be submitted for approval.'))

            route = order._get_approval_route()

            if route == 'manager':
                order.approval_state = 'pending_manager'
                order._assign_approver('manager')
                order.message_post(
                    body=_('Purchase Order submitted for Manager approval. Amount: %s') %
                         order._format_amount(),
                    message_type='notification'
                )
            elif route == 'dept_head_cfo':
                order.approval_state = 'pending_dept_head'
                order._assign_approver('dept_head')
                order.message_post(
                    body=_('Purchase Order submitted for Department Head approval. Amount: %s') %
                         order._format_amount(),
                    message_type='notification'
                )
            else:  # cfo_direct
                order.approval_state = 'pending_cfo_direct'
                order._assign_approver('cfo')
                order.message_post(
                    body=_('Purchase Order submitted for CFO approval (Direct). Amount: %s') %
                         order._format_amount(),
                    message_type='notification'
                )

            order._send_approval_notification()
            order._update_approval_sequence('Submitted for approval')

    def action_approve(self):
        """Approve PO"""
        for order in self:
            if not order._can_approve():
                raise AccessError(_('You are not authorized to approve this purchase order.'))

            current_state = order.approval_state

            if current_state == 'pending_manager':
                order._final_approval()
            elif current_state == 'pending_dept_head':
                order._move_to_cfo_approval()
            elif current_state in ['pending_cfo', 'pending_cfo_direct']:
                order._final_approval()

            order._send_approval_notification()

    def action_reject(self):
        """Reject PO"""
        for order in self:
            if not order._can_approve():
                raise AccessError(_('You are not authorized to reject this purchase order.'))

            return {
                'type': 'ir.actions.act_window',
                'name': _('Reject Purchase Order'),
                'res_model': 'purchase.order.reject.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_purchase_order_id': order.id}
            }

    def _final_approval(self):
        """Complete the approval process"""
        self.approval_state = 'approved'
        self.approved_by = self.env.user
        self.approved_date = fields.Datetime.now()
        self.current_approver = False
        self.message_post(
            body=_('Purchase Order approved by %s') % self.env.user.name,
            message_type='notification'
        )
        self._update_approval_sequence(f'Approved by {self.env.user.name}')

        # Confirm the purchase order
        if self.state in ['draft', 'sent']:
            self.button_confirm()

    def _move_to_cfo_approval(self):
        """Move from dept head to CFO approval"""
        self.approval_state = 'pending_cfo'
        self._assign_approver('cfo')
        self.message_post(
            body=_('Purchase Order approved by Department Head, now pending CFO approval'),
            message_type='notification'
        )
        self._update_approval_sequence(f'Approved by {self.env.user.name} (Dept Head)')

    def _reject_po(self, reason):
        """Reject the PO with reason"""
        self.approval_state = 'rejected'
        self.rejected_by = self.env.user
        self.rejected_date = fields.Datetime.now()
        self.rejection_reason = reason
        self.current_approver = False
        self.message_post(
            body=_('Purchase Order rejected by %s. Reason: %s') % (self.env.user.name, reason),
            message_type='notification'
        )
        self._update_approval_sequence(f'Rejected by {self.env.user.name}: {reason}')

    def _can_approve(self):
        """Check if current user can approve this PO"""
        self.ensure_one()
        user = self.env.user

        if self.approval_state == 'pending_manager':
            return user.has_group('custom_po_approval.group_po_manager')
        elif self.approval_state == 'pending_dept_head':
            return user.has_group('custom_po_approval.group_po_department_head')
        elif self.approval_state in ['pending_cfo', 'pending_cfo_direct']:
            return user.has_group('custom_po_approval.group_po_cfo')

        return False

    def _assign_approver(self, role):
        """Assign the appropriate approver based on role"""
        if role == 'manager':
            group = self.env.ref('custom_po_approval.group_po_manager')
        elif role == 'dept_head':
            group = self.env.ref('custom_po_approval.group_po_department_head')
        elif role == 'cfo':
            group = self.env.ref('custom_po_approval.group_po_cfo')
        else:
            return

        # Get first user in the group (in practice, you might want more sophisticated assignment logic)
        approvers = group.users
        if approvers:
            self.current_approver = approvers[0]

    def _send_approval_notification(self):
        """Send email notification to approver"""
        if not self.current_approver:
            return

        template = self.env.ref('custom_po_approval.email_template_po_approval_notification')
        if template:
            template.send_mail(self.id, force_send=True)

    def _format_amount(self):
        """Format amount for display"""
        return f"{self.currency_id.symbol} {self.amount_total:,.2f}"

    def _update_approval_sequence(self, action):
        """Update approval sequence history"""
        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        new_entry = f"{timestamp}: {action} by {self.env.user.name}"

        if self.approval_sequence:
            self.approval_sequence += f"\n{new_entry}"
        else:
            self.approval_sequence = new_entry

    @api.model
    def create(self, vals):
        """Override create to set initial approval state"""
        if 'approval_state' not in vals:
            vals['approval_state'] = 'draft'
        return super(PurchaseOrder, self).create(vals)


class PurchaseOrderRejectWizard(models.TransientModel):
    _name = 'purchase.order.reject.wizard'
    _description = 'Purchase Order Rejection Wizard'

    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order', required=True)
    rejection_reason = fields.Text(string='Rejection Reason', required=True)

    def action_reject_po(self):
        """Confirm rejection with reason"""
        self.purchase_order_id._reject_po(self.rejection_reason)
        return {'type': 'ir.actions.act_window_close'}