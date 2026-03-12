from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0007_remove_quoteitemimage_expires_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProposalConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cover_image', models.ImageField(
                    blank=True,
                    help_text='Fundo da capa. Tamanho recomendado: A4 portrait (2480×3508 px).',
                    null=True,
                    upload_to='proposal/cover/',
                    verbose_name='Imagem de Capa (página 1)',
                )),
                ('about_image', models.ImageField(
                    blank=True,
                    help_text='Fundo da página Sobre Nós. Tamanho recomendado: A4 portrait.',
                    null=True,
                    upload_to='proposal/about/',
                    verbose_name="Imagem 'Sobre Nós' (página 2)",
                )),
            ],
            options={
                'verbose_name': 'Configuração da Proposta',
                'verbose_name_plural': 'Configuração da Proposta',
            },
        ),
    ]
