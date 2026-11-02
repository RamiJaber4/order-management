"""Create a NEW tiny demo and show full, replay, changes, failure and retry.

Every mutation belongs to the newly generated demo database. Never accepts an
existing database name. The database is retained so the learner can inspect it.
"""
import json

from sqlalchemy import create_engine, text

from etl_analytics.config import engines
from etl_analytics.demo import create_demo
from etl_analytics.load import initialize_target
from etl_analytics import pipeline


class DemoLoadFailure(RuntimeError):
    pass


def inspect_demo(engine):
    with engine.connect() as connection:
        facts = dict(connection.execute(text('''SELECT count(*) AS fact_rows,
            coalesce(sum(quantity),0) AS units,coalesce(sum(line_value),0) AS value
            FROM analytics.fact_order_sales''')).mappings().one())
        checkpoint = dict(connection.execute(text('SELECT watermark,run_id FROM analytics.etl_checkpoints')).mappings().one())
        return {'facts':facts,'checkpoint':checkpoint}


def demonstrate(source_url):
    created = create_demo(source_url,order_count=3)
    engine = create_engine(source_url.set(database=created['database']),hide_parameters=True,
                           connect_args={'connect_timeout':5})
    steps = []
    try:
        initialize_target(engine)
        def run(label):
            report = pipeline.run_pipeline(engine,engine,publish=True,reconcile_source=True,
                                           source_calendar='synthetic_wall_clock',batch_size=2)
            steps.append({'step':label,'report':report,'state':inspect_demo(engine)})
        run('1_initial_full')
        run('2_incremental_without_changes')
        with engine.begin() as connection:
            item = connection.execute(text('''SELECT i.order_id,i.product_id,o.customer_id
                FROM ecommerce.order_items i JOIN ecommerce.orders o ON o.id=i.order_id
                ORDER BY i.order_id,i.product_id LIMIT 1''')).mappings().one()
            connection.execute(text('''UPDATE ecommerce.order_items SET price=price+7
                WHERE order_id=:order_id AND product_id=:product_id'''),item)
            late = connection.execute(text("""INSERT INTO ecommerce.orders(customer_id,order_date)
                VALUES (:customer_id,'2025-12-31') RETURNING id"""),item).scalar_one()
            connection.execute(text('''INSERT INTO ecommerce.order_items(order_id,product_id,quantity,price)
                VALUES (:id,:product,2,25)'''), {'id':late,'product':item['product_id']})
            connection.execute(text('UPDATE ecommerce.products SET stock=stock-2 WHERE id=:id'),
                               {'id':item['product_id']})
        run('3_incremental_new_item_and_old_update')
        with engine.begin() as connection:
            connection.execute(text('''UPDATE ecommerce.order_items SET quantity=quantity+1
                WHERE order_id=:order_id AND product_id=:product_id'''),item)
            connection.execute(text('UPDATE ecommerce.products SET stock=stock-1 WHERE id=:id'),
                               {'id':item['product_id']})
        before = inspect_demo(engine)
        original = pipeline.save_checkpoint
        def fail_after_checkpoint(connection,report,key):
            original(connection,report,key)
            raise DemoLoadFailure()
        pipeline.save_checkpoint = fail_after_checkpoint
        try:
            pipeline.run_pipeline(engine,engine,publish=True,source_calendar='synthetic_wall_clock')
            raise AssertionError('Expected the injected failure')
        except pipeline.PipelineFailure as exc:
            after = inspect_demo(engine)
            assert after == before
            steps.append({'step':'4_injected_failure','report':exc.report,
                          'facts_and_checkpoint_unchanged':True,'state':after})
        finally:
            pipeline.save_checkpoint = original
        run('5_retry_after_failure')
        with engine.connect() as connection:
            runs = [dict(row) for row in connection.execute(text('''
                SELECT mode,status,rows_read,rows_inserted,rows_updated,rows_unchanged,
                       duration_seconds,error_code FROM analytics.etl_runs ORDER BY started_at
            ''')).mappings()]
        return {'database':created['database'],'steps':steps,'runs':runs,
                'note':'rows_read includes overlap replays; unchanged facts are not rewritten'}
    finally:
        engine.dispose()


def main():
    source = target = None
    try:
        source,target = engines()
        result = demonstrate(source.url)
        print(json.dumps(result,default=str,indent=2))
        return 0
    except pipeline.PipelineFailure as exc:
        print(json.dumps(exc.report,default=str))
        return 1
    except Exception as exc:
        print(json.dumps({'status':'failed','reason':type(exc).__name__}))
        return 1
    finally:
        for engine in (source,target):
            if engine is not None:
                engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
