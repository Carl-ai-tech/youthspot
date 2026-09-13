import unittest
from prototype import statistics, documents, retrieve_aws, assemble

class TestHybrid(unittest.TestCase):
    def setUp(self):
        self.rows = {'records': [dict(region=r, age_group=a, year=y, metric='失業率', value=v, unit='%')
            for r,a,y,v in [('新北市','25-29',2025,.064), ('新北市','25-29',2024,.07),
                            ('臺北市','25-29',2025,.078), ('新北市','18-24',2025,.1)]]}
        self.hit = {'content': {'text': '測試用制度說明，非真實政策'}, 'metadata': {
            'title':'測試文件', 'published_at':'2026-01-01', 'source_url':'https://example.org/test', 'access':'public'}}
    def test_exact_scope(self):
        x=statistics(self.rows,region='新北市',band='25-29',year=2025,metrics=['失業率'])
        self.assertEqual([r['value'] for r in x],[.064]);self.assertEqual(x[0]['year'],2025)
    def test_no_substitute(self):
        self.assertEqual(statistics(self.rows,region='新北市',band='25-29',year=2023,metrics=['失業率']),[])
    def test_approved_and_deduplicated(self):
        x,status=documents({'retrievalResults':[self.hit,self.hit]}, {'https://example.org/test'})
        self.assertEqual(len(x),1);self.assertEqual(status,'retrieved');self.assertEqual(x[0]['citation_id'],'D1')
    def test_private_and_unapproved(self):
        self.assertEqual(documents({'retrievalResults':[self.hit]},set())[0],[])
        self.hit['metadata']['access']='private'
        self.assertEqual(documents({'retrievalResults':[self.hit]},{'https://example.org/test'})[0],[])
    def test_guardrail(self):
        self.assertEqual(documents({'guardrailAction':'INTERVENED','retrievalResults':[self.hit]},set())[1],'guardrail_intervened')
    def test_separate_context(self):
        x=assemble(self.rows,{'retrievalResults':[self.hit]}, {'https://example.org/test'},question='q',region='新北市',band='25-29',year=2025,metrics=['失業率','不存在'])
        self.assertEqual(x['evidence']['missing_metrics'],['不存在']);self.assertFalse(x['answer_generated'])
        self.assertIn('D1',x['prompt']);self.assertIn('S1',x['prompt'])
    def test_api_contract_and_errors(self):
        class Client:
            def retrieve(self,**kwargs):return kwargs
        for managed,key in [(True,'managedSearchConfiguration'),(False,'vectorSearchConfiguration')]:
            x=retrieve_aws(Client(),'KB12345678','q',managed=managed)
            self.assertIn(key,x['retrievalConfiguration'])
        class Denied:
            def retrieve(self,**kwargs):raise PermissionError('denied')
        with self.assertRaises(PermissionError):retrieve_aws(Denied(),'KB12345678','q')
if __name__ == '__main__':unittest.main()
