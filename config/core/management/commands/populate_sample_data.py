from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import User, Role
from core.models import Customer, Supplier, SupplierPaymentOption, ShippingCompany
from sales.models import Quote, QuoteItem, QuoteStatus, FreightResponsible


class Command(BaseCommand):
    help = 'Populate database with sample data for testing'

    def handle(self, *args, **options):
        self.stdout.write('Creating sample data...')

        # Create Users (Sellers)
        if not User.objects.filter(username='vendedor1').exists():
            user1 = User.objects.create_user(
                username='vendedor1',
                email='vendedor1@roselar.com',
                password='senha123',
                first_name='João',
                last_name='Silva',
                role=Role.SELLER,
                phone='(11) 98765-4321',
                individual_target_value=Decimal('50000.00')
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Created user: {user1.get_full_name()}'))

        if not User.objects.filter(username='vendedor2').exists():
            user2 = User.objects.create_user(
                username='vendedor2',
                email='vendedor2@roselar.com',
                password='senha123',
                first_name='Maria',
                last_name='Santos',
                role=Role.SELLER,
                phone='(11) 97654-3210',
                individual_target_value=Decimal('45000.00')
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Created user: {user2.get_full_name()}'))

        if not User.objects.filter(username='admin').exists():
            admin = User.objects.create_superuser(
                username='admin',
                email='admin@roselar.com',
                password='admin123',
                first_name='Admin',
                last_name='Roselar',
                role=Role.ADMIN,
                phone='(11) 99999-9999'
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Created admin: {admin.get_full_name()}'))

        # Create Customers (CPF - Pessoa Física)
        customers_pf = [
            {
                'name': 'Carlos Eduardo Mendes',
                'cpf': '123.456.789-10',
                'phone': '(11) 98888-7777',
                'email': 'carlos.mendes@email.com',
                'notes': 'Cliente VIP - Compras frequentes'
            },
            {
                'name': 'Ana Paula Costa',
                'cpf': '987.654.321-00',
                'phone': '(11) 97777-6666',
                'email': 'ana.costa@email.com',
                'notes': 'Preferência por materiais importados'
            },
            {
                'name': 'Roberto Alves Junior',
                'cpf': '456.789.123-45',
                'phone': '(11) 96666-5555',
                'email': 'roberto.junior@email.com',
                'notes': 'Arquiteto - Busca qualidade premium'
            },
        ]

        for customer_data in customers_pf:
            if not Customer.objects.filter(cpf=customer_data['cpf']).exists():
                customer = Customer.objects.create(**customer_data)
                self.stdout.write(self.style.SUCCESS(f'✓ Created customer (PF): {customer.name}'))

        # Create Customers (CNPJ - Pessoa Jurídica)
        customers_pj = [
            {
                'name': 'Construtora São Paulo Ltda',
                'cnpj': '12.345.678/0001-90',
                'phone': '(11) 3333-4444',
                'email': 'contato@construtoraSP.com.br',
                'notes': 'Pedidos grandes - Pagamento 30/60 dias'
            },
            {
                'name': 'Design & Interiores LTDA',
                'cnpj': '98.765.432/0001-10',
                'phone': '(11) 3222-1111',
                'email': 'comercial@designinteriores.com.br',
                'notes': 'Escritório de arquitetura - Projetos residenciais'
            },
            {
                'name': 'Reformas Express ME',
                'cnpj': '45.678.901/0001-23',
                'phone': '(11) 3111-0000',
                'email': 'vendas@reformasexpress.com',
                'notes': 'Parceiro comercial - Desconto corporativo'
            },
        ]

        for customer_data in customers_pj:
            if not Customer.objects.filter(cnpj=customer_data['cnpj']).exists():
                customer = Customer.objects.create(**customer_data)
                self.stdout.write(self.style.SUCCESS(f'✓ Created customer (PJ): {customer.name}'))

        # Create Suppliers
        suppliers = [
            {
                'name': 'Distribuidora ABC Materiais',
                'supplier_number': 'FORN-001',
                'email': 'vendas@abcmateriais.com.br',
                'phone': '(11) 4444-5555',
                'notes': 'Fornecedor principal de pisos e revestimentos'
            },
            {
                'name': 'Madeireira Premium',
                'supplier_number': 'FORN-002',
                'email': 'contato@madeireirapremium.com.br',
                'phone': '(11) 4555-6666',
                'notes': 'Especializada em madeiras nobres e MDF'
            },
            {
                'name': 'Ferragens & Ferramentas Silva',
                'supplier_number': 'FORN-003',
                'email': 'comercial@ferragenssilva.com.br',
                'phone': '(11) 4666-7777',
                'notes': 'Fornecedor de ferragens, fechaduras e acessórios'
            },
            {
                'name': 'Tintas e Acabamentos Costa',
                'supplier_number': 'FORN-004',
                'email': 'vendas@tintascosta.com.br',
                'phone': '(11) 4777-8888',
                'notes': 'Tintas, vernizes e produtos de acabamento'
            },
            {
                'name': 'Iluminação Total Ltda',
                'supplier_number': 'FORN-005',
                'email': 'atendimento@iluminacaototal.com.br',
                'phone': '(11) 4888-9999',
                'notes': 'Luminárias, spots e sistemas de iluminação'
            },
        ]

        for supplier_data in suppliers:
            if not Supplier.objects.filter(email=supplier_data['email']).exists():
                supplier = Supplier.objects.create(**supplier_data)
                self.stdout.write(self.style.SUCCESS(f'✓ Created supplier: {supplier.name}'))

                # Add payment options for each supplier
                payment_options = [
                    {'description': 'À vista', 'days_to_pay': 0, 'is_default': False},
                    {'description': '30 dias', 'days_to_pay': 30, 'is_default': True},
                    {'description': '30/60 dias', 'days_to_pay': 60, 'is_default': False},
                    {'description': '30/60/90 dias', 'days_to_pay': 90, 'is_default': False},
                ]

                for option in payment_options:
                    SupplierPaymentOption.objects.create(
                        supplier=supplier,
                        **option
                    )

        # Create Shipping Companies
        shipping_companies = [
            {
                'name': 'Transportadora Rápida Express',
                'cnpj': '11.222.333/0001-44',
                'phone': '(11) 5555-6666',
                'email': 'contato@rapidaexpress.com.br',
                'contact_person': 'Pedro Oliveira',
                'address': 'Rua das Transportadoras, 123 - São Paulo/SP',
                'payment_methods': 'Boleto bancário\nTransferência\nCartão corporativo',
                'notes': 'Entrega rápida em SP e região metropolitana',
                'is_active': True
            },
            {
                'name': 'Logística Brasil',
                'cnpj': '22.333.444/0001-55',
                'phone': '(11) 5666-7777',
                'email': 'comercial@logisticabrasil.com.br',
                'contact_person': 'Juliana Santos',
                'address': 'Av. Logística, 456 - Guarulhos/SP',
                'payment_methods': 'Boleto bancário\nPIX\nTransferência',
                'notes': 'Cobertura nacional - Bom custo-benefício',
                'is_active': True
            },
            {
                'name': 'Cargas Pesadas Transporte',
                'cnpj': '33.444.555/0001-66',
                'phone': '(11) 5777-8888',
                'email': 'atendimento@cargaspesadas.com.br',
                'contact_person': 'Marcos Ferreira',
                'address': 'Rodovia dos Transportes, Km 23 - Osasco/SP',
                'payment_methods': 'Boleto bancário\nCheque\nTransferência',
                'notes': 'Especializada em cargas grandes e pesadas',
                'is_active': True
            },
        ]

        for shipping_data in shipping_companies:
            if not ShippingCompany.objects.filter(email=shipping_data['email']).exists():
                shipping = ShippingCompany.objects.create(**shipping_data)
                self.stdout.write(self.style.SUCCESS(f'✓ Created shipping company: {shipping.name}'))

        # Create a sample Quote with Items
        first_customer = Customer.objects.first()
        first_seller = User.objects.filter(role=Role.SELLER).first()
        first_supplier = Supplier.objects.first()
        second_supplier = Supplier.objects.all()[1] if Supplier.objects.count() > 1 else first_supplier

        if first_customer and first_seller and not Quote.objects.filter(number='ORC-0001').exists():
            quote = Quote.objects.create(
                number='ORC-0001',
                customer=first_customer,
                seller=first_seller,
                quote_date=timezone.now().date(),
                delivery_deadline=timezone.now().date() + timezone.timedelta(days=15),
                status=QuoteStatus.DRAFT,
                freight_responsible=FreightResponsible.STORE,
                freight_value=Decimal('150.00'),
                payment_description='30/60 dias',
                total_value_snapshot=Decimal('8500.00')
            )

            # Add quote items
            items = [
                {
                    'supplier': first_supplier,
                    'product_name': 'Piso Laminado Premium',
                    'description': 'Piso laminado cor carvalho, 8mm espessura, caixa com 2,5m²',
                    'quantity': 20,
                    'unit_value': Decimal('89.90'),
                    'condition_text': 'Disponibilidade imediata'
                },
                {
                    'supplier': first_supplier,
                    'product_name': 'Rodapé MDF Branco',
                    'description': 'Rodapé em MDF, 10cm altura, 2,20m comprimento',
                    'quantity': 15,
                    'unit_value': Decimal('24.50'),
                    'condition_text': 'Entrega em 7 dias'
                },
                {
                    'supplier': second_supplier,
                    'product_name': 'Tinta Acrílica Branco Neve',
                    'description': 'Tinta acrílica premium, galão 18L, rendimento 250m²',
                    'quantity': 4,
                    'unit_value': Decimal('189.00'),
                    'condition_text': 'Pronta entrega'
                },
                {
                    'supplier': second_supplier,
                    'product_name': 'Luminária LED Embutir',
                    'description': 'Luminária LED 12W, luz branca, quadrada 17x17cm',
                    'quantity': 8,
                    'unit_value': Decimal('45.00'),
                    'condition_text': 'Disponível'
                },
            ]

            for item_data in items:
                QuoteItem.objects.create(quote=quote, **item_data)

            self.stdout.write(self.style.SUCCESS(f'✓ Created sample quote: {quote.number} with {len(items)} items'))

        self.stdout.write(self.style.SUCCESS('\n✅ Sample data population completed!'))
        self.stdout.write(self.style.WARNING('\nLogin credentials:'))
        self.stdout.write('  Username: vendedor1 | Password: senha123')
        self.stdout.write('  Username: vendedor2 | Password: senha123')
        self.stdout.write('  Username: admin | Password: admin123')
