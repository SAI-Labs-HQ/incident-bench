"""Fictional operational records for the isolated document library."""
import csv
import io
import random
from datetime import date, timedelta


def document(bucket, number, rows=105000):
    rng = random.Random(f'{bucket}/{number}')
    period = date(2026, 10, 1) - timedelta(days=30 * number)
    office = ('North depot', 'Airport desk', 'Central office', 'West depot')[number % 4]
    stream = io.StringIO()
    stream.write(f'Alder Mobility fleet operations | {office}\n')
    stream.write(f'Report period: {period.isoformat()} to {(period + timedelta(days=29)).isoformat()}\n')
    stream.write('Prepared for account review. Amounts are in USD; mileage is recorded at vehicle return.\n')
    stream.write('Inspection references identify the signed vehicle condition record retained by operations.\n\n')
    writer = csv.writer(stream)
    writer.writerow(('contract', 'date', 'vehicle', 'class', 'days', 'mileage_out', 'mileage_in',
                     'rental_amount', 'fuel', 'condition', 'inspection_reference', 'return_note'))
    for row in range(rows + rng.randrange(3000)):
        days = rng.randint(1, 21)
        mileage = rng.randint(1200, 84000)
        writer.writerow((f'CT-{number+1:02d}-{row+1:06d}',
                         (period + timedelta(days=rng.randrange(30))).isoformat(),
                         f'VH-{rng.randrange(100, 9000):04d}', rng.choice(('compact', 'sedan', 'van', 'SUV')),
                         days, mileage, mileage + days * rng.randint(18, 210),
                         f'{days * rng.randint(3900, 12900) / 100:.2f}',
                         rng.choice(('full', 'three-quarters', 'half', 'quarter')),
                         rng.choice(('clear', 'reviewed', 'repair scheduled')),
                         f'IN-{rng.getrandbits(48):012X}',
                         rng.choice(('Keys and accessories received', 'Return photos attached',
                                     'Fuel adjustment accepted', 'Cleaning completed',
                                     'Windscreen inspection completed', 'Mileage verified at desk'))))
    stream.write('\nPrepared by Operations Reporting. Refer discrepancies to the account review queue.\n')
    return stream.getvalue().encode()


def review_register():
    """Sales' export of the current review queue, rendered in the offline preview."""
    rng = random.Random('sales-review-register')
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(('reference', 'office', 'vehicle', 'status', 'review_note'))
    for row in range(18000):
        writer.writerow((f'RV-2026-{row+1:06d}',
                         rng.choice(('North depot', 'Airport desk', 'Central office', 'West depot')),
                         f'VH-{rng.randrange(100, 9000):04d}',
                         rng.choice(('Ready for review', 'Awaiting signature', 'Reviewed')),
                         rng.choice(('Return photos and inspection references are available in the account document record.',
                                     'Mileage and fuel adjustment have been reconciled with the signed rental agreement.',
                                     'Extension terms have been checked against availability and the customer approval.'))))
    return stream.getvalue()
