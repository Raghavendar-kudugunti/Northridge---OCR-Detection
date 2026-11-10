"""Build a varied set of designed, clearly synthetic sample financial documents."""
import csv
from datetime import date, timedelta
from pathlib import Path
import pymupdf

OUT = Path(__file__).parent / 'documents'
NAMES = ['Ravi Menon', 'Meera Iyer', 'Kiran Rao', 'Asha Nair', 'Anil Kumar', 'Leela Shah',
         'Dev Patel', 'Nina Das', 'Arjun Sen', 'Maya Roy', 'Kabir Jain', 'Tara Bose']
LOCATIONS = ['Kochi', 'Pune', 'Mysuru', 'Indore', 'Jaipur', 'Surat', 'Nagpur', 'Vadodara',
             'Coimbatore', 'Nashik', 'Bhopal', 'Mangaluru']
NAVY = (0.075, 0.12, 0.22)
BLUE = (0.13, 0.35, 0.68)
PALE = (0.94, 0.96, 0.985)
INK = (0.12, 0.16, 0.22)
MUTED = (0.40, 0.45, 0.53)
GREEN = (0.10, 0.43, 0.31)
ORANGE = (0.89, 0.52, 0.13)


def text(page, x, y, value, size=10, color=INK, bold=False):
    page.insert_text((x, y), str(value), fontname='hebo' if bold else 'helv', fontsize=size, color=color)


def box(page, rect, fill, stroke=None, radius=6, width=0.8):
    page.draw_rect(rect, color=stroke or fill, fill=fill, width=width, radius=min(.5, radius/100))


def base_page(title, category, number):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect((0, 0, 595, 92), color=NAVY, fill=NAVY)
    page.draw_circle((48, 45), 22, color=BLUE, fill=BLUE)
    text(page, 37, 50, 'H', 18, (1, 1, 1), True)
    text(page, 82, 36, 'HARBOUR', 14, (1, 1, 1), True)
    text(page, 82, 56, 'FINANCIAL SERVICES', 8, (0.77, 0.83, 0.92), True)
    text(page, 82, 74, 'SAMPLE DOCUMENT SERIES', 7, (0.62, 0.71, 0.84))
    text(page, 38, 127, title, 20, NAVY, True)
    text(page, 38, 147, category.upper(), 8, BLUE, True)
    page.draw_line((38, 160), (557, 160), color=(0.84, 0.87, 0.91), width=0.8)
    # This prominent label keeps generated samples from being mistaken for real records.
    box(page, (405, 105, 557, 132), (1.0, 0.95, 0.86), stroke=(0.91, 0.72, 0.45), radius=5)
    text(page, 417, 122, f'SYNTHETIC SAMPLE {number:02d}', 7.5, (0.53, 0.30, 0.07), True)
    return doc, page


def footer(page, number):
    page.draw_line((38, 795), (557, 795), color=(0.86, 0.88, 0.91), width=0.7)
    text(page, 38, 813, 'Generated for software testing only. All names, balances and references are fictitious.', 7.5, MUTED)
    text(page, 520, 813, f'{number:02d} / 64', 7, MUTED)


def identity_card(name, i, number):
    doc, page = base_page('Identity card', 'Synthetic identity record', number)
    # Card frame with a colored rail and a deliberately illustrated avatar, never a real portrait.
    box(page, (38, 185, 557, 468), (0.985, 0.99, 1.0), stroke=(0.77, 0.82, 0.89), radius=12, width=1.1)
    page.draw_rect((38, 185, 50, 468), color=BLUE, fill=BLUE)
    page.draw_rect((50, 185, 557, 238), color=PALE, fill=PALE)
    text(page, 70, 207, 'NORTHBRIDGE BORROWER IDENTIFICATION', 10, NAVY, True)
    text(page, 70, 225, 'Training credential · not issued by a government authority', 8, MUTED)
    # Stylized initials avatar
    page.draw_circle((115, 316), 44, color=(0.79, 0.85, 0.94), fill=(0.79, 0.85, 0.94))
    page.draw_circle((115, 301), 15, color=(0.38, 0.51, 0.69), fill=(0.38, 0.51, 0.69))
    page.draw_rect((88, 316, 142, 354), color=(0.38, 0.51, 0.69), fill=(0.38, 0.51, 0.69), radius=.5)
    text(page, 78, 385, 'SAMPLE PROFILE', 7, MUTED, True)
    text(page, 188, 274, 'Full name', 8, MUTED)
    text(page, 188, 296, name, 15, NAVY, True)
    text(page, 188, 331, 'Date of birth', 8, MUTED)
    dob = date(1989 + (i % 10), 1 + (i % 12), 8 + (i % 18)).strftime('%d/%m/%Y')
    text(page, 188, 351, dob, 11, INK)
    text(page, 188, 382, 'ID number', 8, MUTED)
    text(page, 188, 402, f'TESTA{1000+i:04d}Q', 11, INK, True)
    text(page, 70, 438, 'FICTITIOUS TEST RECORD  •  NO OFFICIAL VALIDITY', 7.5, ORANGE, True)
    box(page, (38, 492, 557, 548), PALE, radius=6)
    text(page, 55, 515, 'Verification note', 8, BLUE, True)
    text(page, 55, 534, 'This sample credential has no real-world identity or legal standing.', 9, INK)
    footer(page, number)
    data=doc.tobytes(); doc.close(); return data, dob, f'TESTA{1000+i:04d}Q'


def aadhaar_sample(name, i, number):
    """A fictional Aadhaar-style OCR sample with an intentionally invalid placeholder number."""
    doc, page = base_page('Aadhaar-style sample card', 'Identity data · not a government document', number)
    box(page, (38, 184, 557, 520), (0.99, 0.99, 0.975), stroke=(0.83, 0.84, 0.80), radius=10, width=1)
    page.draw_rect((38, 184, 557, 238), color=(0.91, 0.47, 0.20), fill=(0.91, 0.47, 0.20))
    text(page, 58, 207, 'AADHAAR DATA · OCR TRAINING SAMPLE', 11, (1,1,1), True)
    text(page, 58, 226, 'Fictional identity record / not issued by UIDAI', 8, (1,1,1))
    text(page, 61, 280, 'Name', 8, MUTED)
    text(page, 61, 302, name, 15, NAVY, True)
    dob = date(1989 + (i % 10), 1 + (i % 12), 8 + (i % 18)).strftime('%d/%m/%Y')
    text(page, 61, 337, 'Date of birth', 8, MUTED)
    text(page, 61, 357, dob, 11, INK)
    text(page, 61, 390, 'Address', 8, MUTED)
    text(page, 61, 410, f'{LOCATIONS[i % len(LOCATIONS)]} Sample Lane', 10, INK)
    text(page, 61, 429, 'Fictional Nagar · PIN 000000', 9, INK)
    box(page, (61, 448, 362, 502), (1.0, 0.95, 0.89), stroke=(0.91, 0.72, 0.45), radius=5)
    text(page, 75, 465, 'Aadhaar number', 7, (0.57, 0.31, 0.10), True)
    text(page, 75, 485, '0000 0000 0000', 14, (0.57, 0.31, 0.10), True)
    text(page, 75, 497, 'INVALID PLACEHOLDER NUMBER', 6.5, (0.57, 0.31, 0.10), True)
    text(page, 396, 347, 'SYNTHETIC', 12, ORANGE, True)
    text(page, 396, 367, 'NO QR CODE', 8, MUTED, True)
    text(page, 396, 383, 'NO OFFICIAL SEAL', 8, MUTED, True)
    text(page, 396, 399, f'Sample {number:02d}', 8, MUTED)
    footer(page, number)
    data=doc.tobytes(); doc.close(); return data, dob, '0000 0000 0000'


def pan_sample(name, i, number):
    """A fictional PAN-card-style OCR sample; its identifier is not PAN-shaped or usable."""
    doc, page = base_page('PAN-style sample card', 'Permanent account number · fictional example', number)
    box(page, (38, 184, 557, 520), (0.985, 0.99, 1.0), stroke=(0.77, 0.82, 0.89), radius=10, width=1)
    page.draw_rect((38, 184, 557, 240), color=(0.15, 0.34, 0.61), fill=(0.15, 0.34, 0.61))
    text(page, 58, 207, 'PERMANENT ACCOUNT DATA', 12, (1,1,1), True)
    text(page, 58, 227, 'FICTIONAL PAN SAMPLE · NOT ISSUED BY INCOME TAX DEPARTMENT', 7.5, (1,1,1), True)
    text(page, 61, 286, 'Name', 8, MUTED)
    text(page, 61, 309, name, 15, NAVY, True)
    text(page, 61, 347, 'Parent / guardian name', 8, MUTED)
    text(page, 61, 367, 'NOT PROVIDED · TRAINING DATA ONLY', 9, INK)
    text(page, 61, 402, 'Date of birth', 8, MUTED)
    dob = date(1989 + (i % 10), 1 + (i % 12), 8 + (i % 18)).strftime('%d/%m/%Y')
    text(page, 61, 422, dob, 10, INK)
    box(page, (61, 448, 362, 502), (0.93, 0.96, 0.99), stroke=(0.72, 0.80, 0.90), radius=5)
    pan = f'SAMPLE-TAX-{i+1:04d}'
    text(page, 75, 466, 'PAN number', 7, BLUE, True)
    text(page, 75, 488, pan, 13, BLUE, True)
    text(page, 396, 341, 'NOT VALID', 15, ORANGE, True)
    text(page, 396, 365, 'Fictional code', 8, MUTED)
    text(page, 396, 383, 'No signature', 8, MUTED)
    text(page, 396, 401, 'No security marks', 8, MUTED)
    footer(page, number)
    data=doc.tobytes(); doc.close(); return data, dob, pan


def salary_slip(name, i, number, employer='Harbour Tools Pvt Ltd'):
    doc, page = base_page('Monthly salary statement', 'Payroll · October 2026', number)
    gross = 48000 + (i * 3850) % 57000
    basic = round(gross * .58 / 100) * 100
    housing = round(gross * .27 / 100) * 100
    allowance = gross - basic - housing
    tax = round(gross * .07 / 100) * 100
    retirement = round(gross * .048 / 100) * 100
    net = gross - tax - retirement
    box(page, (38, 180, 557, 263), PALE, radius=8)
    text(page, 55, 203, 'EMPLOYEE NAME', 7, MUTED, True)
    text(page, 55, 226, name, 13, NAVY, True)
    text(page, 55, 246, f'Employee code  HT-{2300+i:04d}', 8.5, MUTED)
    text(page, 310, 203, 'PAY PERIOD', 7, MUTED, True)
    text(page, 310, 226, '01 Oct – 31 Oct 2026', 10, INK, True)
    text(page, 310, 246, f'Payment date  31 Oct 2026', 8.5, MUTED)
    text(page, 49, 302, 'EARNINGS', 8, BLUE, True)
    text(page, 330, 302, 'DEDUCTIONS', 8, BLUE, True)
    page.draw_line((48, 312), (546, 312), color=(0.82, 0.85, 0.90), width=.7)
    rows=[('Basic pay',basic),('House rent allowance',housing),('Other allowance',allowance)]
    deductions=[('Income tax',tax),('Retirement contribution',retirement)]
    y=338
    for label, amount in rows:
        text(page, 50, y, label, 9, INK); text(page, 250, y, f'{amount:,.2f}', 9, INK)
        y+=28
    y=338
    for label, amount in deductions:
        text(page, 331, y, label, 8.5, INK); text(page, 492, y, f'{amount:,.2f}', 8.5, INK)
        y+=28
    box(page, (48, 432, 547, 476), (0.91, 0.96, 0.93), radius=6)
    text(page, 61, 450, 'Gross monthly salary', 9, GREEN, True)
    text(page, 228, 460, f'INR {gross:,.2f}', 12, GREEN, True)
    text(page, 336, 450, 'Net pay', 9, GREEN, True)
    text(page, 445, 460, f'INR {net:,.2f}', 12, GREEN, True)
    text(page, 61, 492, f'Gross monthly salary: INR {gross:,.2f}     Net pay: INR {net:,.2f}', 8, MUTED)
    box(page, (48, 506, 547, 566), PALE, radius=6)
    text(page, 62, 530, 'EMPLOYER', 7, MUTED, True)
    text(page, 62, 551, f'Employer: {employer}', 9, NAVY, True)
    text(page, 350, 530, 'WORK LOCATION', 7, MUTED, True)
    text(page, 350, 551, f'{LOCATIONS[i % len(LOCATIONS)]} office', 9, INK)
    footer(page, number)
    data=doc.tobytes(); doc.close(); return data, gross, net


def bank_statement(name, i, number):
    doc, page = base_page('Account statement', 'Everyday savings · 01–31 October 2026', number)
    account = f'6000100000{100+i:04d}'
    opening = 28400 + i * 1730
    salary = 48000 + (i * 3850) % 57000
    net_salary = salary - round(salary * .118 / 100) * 100
    rows=[('01 Oct 2026','UPI / HOUSEHOLD SUPPLIES','2,450.00','—'),
          ('04 Oct 2026','CARD / GROCER MART','1,286.50','—'),
          ('08 Oct 2026','NEFT / RENT PAYMENT','12,000.00','—'),
          ('12 Oct 2026','UPI / METRO TRANSIT','685.00','—'),
          ('18 Oct 2026','CARD / PHARMACY','948.00','—'),
          ('25 Oct 2026','SALARY CREDIT / HARBOUR TOOLS','—',f'{net_salary:,.2f}'),
          ('27 Oct 2026','UPI / UTILITIES','2,140.00','—'),
          ('30 Oct 2026','INTEREST CREDIT','—','184.00')]
    box(page, (38, 179, 557, 271), PALE, radius=8)
    text(page, 55, 201, 'ACCOUNT HOLDER', 7, MUTED, True)
    text(page, 55, 224, name, 12, NAVY, True)
    text(page, 55, 245, f'{LOCATIONS[i % len(LOCATIONS)]} · Sample branch', 8.5, MUTED)
    text(page, 342, 201, 'ACCOUNT NUMBER', 7, MUTED, True)
    text(page, 342, 224, account, 10, INK, True)
    text(page, 342, 245, f'Statement ref  HFS-26-10-{4100+i}', 8, MUTED)
    text(page, 55, 263, f'Opening balance: INR {opening:,.2f}', 8, MUTED)
    box(page, (38, 291, 557, 337), NAVY, radius=4)
    labels=[('DATE',51),('TRANSACTION DETAILS',151),('DEBIT (INR)',380),('CREDIT (INR)',477)]
    for label,x in labels: text(page,x,318,label,7,(1,1,1),True)
    balance=opening; y=363
    for idx,(day,description,debit,credit) in enumerate(rows):
        if debit!='—': balance-=float(debit.replace(',',''))
        if credit!='—': balance+=float(credit.replace(',',''))
        if idx%2==0: page.draw_rect((38,y-19,557,y+7),color=(0.97,0.98,0.99),fill=(0.97,0.98,0.99))
        text(page,50,y,day,7.3,MUTED)
        text(page,151,y,description,7.5,INK,description.startswith('SALARY'))
        text(page,382,y,debit,7.5,INK)
        text(page,480,y,credit,7.5,GREEN if credit!='—' else INK)
        y+=31
    page.draw_line((38,y-12),(557,y-12),color=(0.82,0.85,0.90),width=.7)
    box(page,(38,y+5,557,y+65),(0.91,0.96,0.93),radius=6)
    text(page,54,y+29,'Closing balance',9,GREEN,True)
    text(page,425,y+29,f'INR {balance:,.2f}',11,GREEN,True)
    text(page,54,y+49,'Total debits and credits shown for this sample period',7.2,MUTED)
    footer(page,number)
    data=doc.tobytes(); doc.close(); return data, account, opening


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]; index=0
    for i,name in enumerate(NAMES):
        outputs=[('id_card',identity_card(name,i,index+1)),
                 ('aadhaar_sample',aadhaar_sample(name,i,index+2)),
                 ('pan_sample',pan_sample(name,i,index+3)),
                 ('salary_slip',salary_slip(name,i,index+4)),
                 ('bank_statement',bank_statement(name,i,index+5))]
        for kind,(data,*details) in outputs:
            index+=1
            filename=f'synthetic_{index:02d}_{kind}_{i+1:02d}.pdf'
            (OUT/filename).write_bytes(data)
            rows.append({'filename':filename,'document_type':kind,'synthetic':True,
                         'fictitious_holder':name,'details':'; '.join(map(str,details))})
    # Four alternate layouts add controlled variety while preserving parseable labels.
    extras=[('id_card',identity_card('Ravi Menon',0,61)),
            ('salary_slip',salary_slip('Anil Kumar',4,62,'Ravi Menon Industries')),
            ('bank_statement',bank_statement('Meera Iyer',1,63)),
            ('salary_slip',salary_slip('Kiran Rao',2,64))]
    for j,(kind,(data,*details)) in enumerate(extras,61):
        filename=f'synthetic_{j:02d}_{kind}_variant.pdf'
        (OUT/filename).write_bytes(data)
        rows.append({'filename':filename,'document_type':kind,'synthetic':True,
                     'fictitious_holder':['Ravi Menon','Anil Kumar','Meera Iyer','Kiran Rao'][j-61],
                     'details':'; '.join(map(str,details))})
    with (OUT/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['filename','document_type','synthetic','fictitious_holder','details'])
        writer.writeheader(); writer.writerows(rows)
    print(f'Wrote {len(rows)} designed synthetic PDFs to {OUT}')

if __name__=='__main__': main()
