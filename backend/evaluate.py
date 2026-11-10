"""Reproducible synthetic 20-document smoke benchmark; not real-world accuracy."""
import io
import json
import csv
from pathlib import Path
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .pipeline import build_analyzer, process, normalize_name
from .ocr import available_engines


FONT_CANDIDATES=('DejaVuSans.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/Library/Fonts/Arial.ttf',
    '/System/Library/Fonts/Supplemental/Arial.ttf','arial.ttf','C:/Windows/Fonts/arial.ttf')


def load_font(size=32):
    """DejaVu Sans (Docker/Linux) or Arial (macOS/Windows), so image tests run on every OS."""
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate,size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_document(kind,name,index,image=False):
    text = (f'IDENTITY CARD\nFull name: {name}\nDate of birth: 14/08/1994\nID number: ABCDE1234F' if kind=='ID card' else
        f'SALARY SLIP\nEmployee name: {name}\nEmployer: Harbour Tools Pvt Ltd\nGross monthly salary: INR 82,000.00\nNet pay: INR 72,200.00' if kind=='Salary slip' else
        f'BANK STATEMENT\nAccount holder: {name}\nAccount number: 12345678901234\nOpening balance: INR 120000')
    text += f'\nSYNTHETIC EVALUATION DOCUMENT {index:02d}\nNot a real identity or financial record.'
    if image:
        canvas=Image.new('RGB',(1500,700),'white')
        font=load_font(32)
        ImageDraw.Draw(canvas).multiline_text((50,50),text,font=font,fill='black',spacing=18)
        stream=io.BytesIO();canvas.save(stream,format='PNG');return stream.getvalue()
    doc=pymupdf.open();page=doc.new_page();page.insert_text((50,65),text,fontsize=14)
    data=doc.tobytes();doc.close();return data


def degrade(png_bytes, index):
    """Phone-photo-like scan: slight rotation, blur, uneven light and JPEG compression."""
    image=Image.open(io.BytesIO(png_bytes)).convert('RGB')
    image=image.rotate((-1)**index*(1.5+index%3), expand=True, fillcolor=(222,218,208))
    shade=Image.linear_gradient('L').resize(image.size).point(lambda v: 255-v//5)
    image=Image.composite(image, Image.new('RGB', image.size, (150,150,150)), shade)
    image=image.filter(ImageFilter.GaussianBlur(1.1))
    stream=io.BytesIO(); image.save(stream, format='JPEG', quality=45); return stream.getvalue()


def score(doc, kind, name, employer='Harbour Tools Pvt Ltd'):
    expected={'full_name':name,'date_of_birth':None,'id_number':None,'employer_name':None,'monthly_salary':None}
    if kind=='ID card': expected.update(date_of_birth='1994-08-14', id_number='ABCDE1234F')
    if kind=='Salary slip': expected.update(employer_name=employer, monthly_salary=82000)
    correct=0
    for field,value in expected.items():
        actual=getattr(doc.fields,field)
        correct+=(normalize_name(actual)==normalize_name(value) if isinstance(actual,str) and isinstance(value,str) else actual==value)
    return correct, len(expected)


def run_suite(analyzer, engine):
    cases=[];correct_type=correct_fields=total_fields=correct_rule=0;number=0
    # 8 pairs (4 matches and 4 mismatches), ID-only, salary-only, 2 bank docs = 20 docs.
    requests=[]
    pairs=[('Ravi Menon','RAVI MENON','Verified','case-only normalized match'),
           ('Meera Shah','Meera Shah','Verified','exact match'),
           ('Kiran Rao','KIRAN RAO','Verified','case-only match'),
           ('Asha Nair','Asha Nair','Verified','exact match'),
           ('Ravi Menon','Ravi M Menon','Mismatch','middle initial'),
           ('Ravi Menon','Anil Kumar','Mismatch','different person'),
           ('Meera Iyer','Mira Iyer','Mismatch','name spelling difference'),
           ('Ravi Menon','Anil Kumar','Mismatch','employer name nearby; employee label wins')]
    for i,(id_name,pay_name,status,note) in enumerate(pairs):
        items=[('ID card',id_name),('Salary slip',pay_name)]
        if i==7: items[1]=('Salary slip',pay_name+'\nEmployer: Ravi Menon Industries')
        requests.append((items,status,note))
    requests += [([('ID card','Ravi Menon')],'Incomplete','ID only'),
                 ([('Salary slip','Ravi Menon')],'Incomplete','salary slip only')]
    requests += [([('Bank statement, page 1','Ravi Menon')],'Not applicable','bank only') for _ in range(2)]
    for items,expected,note in requests:
        payload=[]
        for kind,name in items:
            number+=1
            payload.append(make_document(kind,name,number,image=number in [2,4,17,20]))
        result=process(payload,analyzer,engine=engine)
        correct_rule+=result.check.status==expected
        for doc,(kind,name) in zip(result.documents,items):
            correct_type+=doc.document_type==kind
            person=name.split('\n')[0]
            expected_fields={'full_name':person,'date_of_birth':None,'id_number':None,'employer_name':None,'monthly_salary':None}
            if kind=='ID card': expected_fields.update(date_of_birth='1994-08-14',id_number='ABCDE1234F')
            if kind=='Salary slip': expected_fields.update(employer_name='Ravi Menon Industries' if 'Industries' in name else 'Harbour Tools Pvt Ltd',monthly_salary=82000)
            ok,total=score(doc,kind,person,expected_fields['employer_name'] or 'Harbour Tools Pvt Ltd'); correct_fields+=ok; total_fields+=total
            cases.append({'document':len(cases)+1,'request_id':result.request_id,'scenario':note,
                'expected_type':kind,'actual_type':doc.document_type,'engine':doc.pipeline.ocr_engine,
                'expected_fields':expected_fields,'actual_fields':doc.fields.model_dump(),
                'fields_correct':ok,'fields_total':total,'expected_rule':expected,'actual_rule':result.check.status,'review_required':result.check.review_required,
                'name_confidence':doc.field_evidence['full_name'].confidence,
                'salary_confidence':doc.field_evidence['monthly_salary'].confidence if doc.fields.monthly_salary is not None else None})
    return {'field_match':{'correct':correct_fields,'total':total_fields,'percent':round(correct_fields/total_fields*100,2)},
        'document_type':{'correct':correct_type,'total':20,'percent':round(correct_type/20*100,2)},
        'rule_accuracy':{'correct':correct_rule,'total':12,'percent':round(correct_rule/12*100,2)},'cases':cases}


def run_degraded(analyzer, engine):
    """8 photo-like scans (4 ID cards, 4 salary slips): accuracy, review flags, OCR time."""
    rows=[];correct=total=failed=reviews=0;ocr_ms=[]
    for index in range(8):
        kind='ID card' if index%2==0 else 'Salary slip'
        data=degrade(make_document(kind,'Ravi Menon',100+index,image=True),index)
        try:
            doc=process([data],analyzer,engine=engine).documents[0]
        except Exception:
            failed+=1; rows.append({'scan':index+1,'kind':kind,'read':False}); continue
        ok,count=score(doc,kind,'Ravi Menon'); correct+=ok; total+=count
        needs=any(ev.needs_review for key,ev in doc.field_evidence.items() if ev.source!='none' and key in ('full_name','monthly_salary'))
        reviews+=needs; ocr_ms.append(doc.pipeline.timings_ms.ocr)
        rows.append({'scan':index+1,'kind':kind,'read':True,'fields_correct':f'{ok}/{count}','ocr_confidence':doc.pipeline.confidence,
                     'name_confidence':doc.field_evidence['full_name'].confidence,'gated_field_below_threshold':needs,'ocr_ms':doc.pipeline.timings_ms.ocr})
    return {'scans':8,'unreadable':failed,'field_match':{'correct':correct,'total':total,'percent':round(correct/total*100,2) if total else 0},
            'below_review_threshold':reviews,'median_ocr_ms':sorted(ocr_ms)[len(ocr_ms)//2] if ocr_ms else None,'cases':rows}


def main():
    analyzer=build_analyzer()
    engines=[e for e in ('rapidocr','tesseract') if e in available_engines()]
    report={'scope':'Synthetic template smoke benchmark; does not establish real-world accuracy. '
                    '16 embedded-text PDFs + 4 clean PNG scans per engine, plus 8 degraded photo-like scans per engine. '
                    'Includes four matching and four mismatching name pairs; case-only differences match by the specified normalization.',
            'documents':20,'requests':12,'engines':{}}
    for engine in engines:
        report['engines'][engine]={'clean_suite':run_suite(analyzer,engine),'degraded_scans':run_degraded(analyzer,engine)}
    target=Path(__file__).parent.parent/'evaluation-results.json'
    target.write_text(json.dumps(report,indent=2)+'\n')
    sheet=Path(__file__).parent.parent/'samples'/'20-document-test-sheet.csv'
    sheet.parent.mkdir(parents=True,exist_ok=True)
    rows=report['engines'][engines[0]]['clean_suite']['cases'] if engines else []
    with sheet.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=['document','request_id','scenario','expected_type','actual_type','expected_fields','actual_fields','fields_correct','fields_total','expected_rule','actual_rule','review_required'],extrasaction='ignore')
        writer.writeheader()
        for row in rows: writer.writerow({**row,'expected_fields':json.dumps(row['expected_fields'],ensure_ascii=False),'actual_fields':json.dumps(row['actual_fields'],ensure_ascii=False)})
    summary={e:{'clean_fields':r['clean_suite']['field_match']['percent'],'clean_rules':r['clean_suite']['rule_accuracy']['percent'],
                'degraded_fields':r['degraded_scans']['field_match']['percent'],'degraded_unreadable':r['degraded_scans']['unreadable'],
                'degraded_below_threshold':r['degraded_scans']['below_review_threshold'],'degraded_median_ocr_ms':r['degraded_scans']['median_ocr_ms']}
             for e,r in report['engines'].items()}
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
