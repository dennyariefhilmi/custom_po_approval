{
    'name': 'Purchase Order Multi-Level Approval',
    'version': '18.0.1.0.0',
    'category': 'Purchase',
    'summary': 'Multi-level approval workflow for Purchase Orders based on amount',
    'description': '''
        This module extends the Purchase Order workflow with multi-level approval:
        - Less than IDR 5 million: Manager approval
        - IDR 5-20 million: Department Head then CFO approval
        - More than IDR 20 million: CFO approval
    ''',
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['purchase', 'mail'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/mail_template.xml',
        'views/purchase_order_view.xml',
    ],
    'demo': [],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}