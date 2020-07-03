"""Implementation of the REST API for the backbone service."""

import os
import logging
import flask
import time
from f8a_worker.setup_celery import init_selinon
from flask import Flask, request, current_app
from flask_cors import CORS
from raven.contrib.flask import Sentry

from src.recommender import RecommendationTask as RecommendationTaskV1
from src.stack_aggregator import StackAggregator as StackAggregatorV1
from src.v2.recommender import RecommendationTask as RecommendationTaskV2
from src.v2.stack_aggregator import StackAggregator as StackAggregatorV2
from src.utils import push_data, total_time_elapsed, get_time_delta


def setup_logging(flask_app):
    """Perform the setup of logging (file, log level) for this application."""
    if not flask_app.debug:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s:%(lineno)d: %(message)s'))
        log_level = os.environ.get('FLASK_LOGGING_LEVEL', logging.getLevelName(logging.WARNING))
        handler.setLevel(log_level)

        flask_app.logger.addHandler(handler)
        flask_app.config['LOGGER_HANDLER_POLICY'] = 'never'
        flask_app.logger.setLevel(logging.DEBUG)


app = Flask(__name__)
setup_logging(app)
CORS(app)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
sentry = Sentry(app, dsn=SENTRY_DSN, logging=True, level=logging.ERROR)

init_selinon()


@app.route('/api/readiness')
def readiness():
    """Handle GET requests that are sent to /api/readiness REST API endpoint."""
    return flask.jsonify({}), 200


@app.route('/api/liveness')
def liveness():
    """Handle GET requests that are sent to /api/liveness REST API endpoint."""
    return flask.jsonify({}), 200


def _recommender(handler):
    eri = 'UNKNOWN'
    recommender_started_at = time.time()

    r = {'recommendation': 'failure', 'external_request_id': None}
    # (fixme) Create decorator for metrics handling.
    metrics_payload = {
        'pid': os.getpid(),
        'hostname': os.environ.get("HOSTNAME"),
        'endpoint': request.endpoint,
        'request_method': request.method,
        'status_code': 200
    }

    input_json = request.get_json()
    if input_json and 'external_request_id' in input_json and input_json['external_request_id']:
        eri = input_json['external_request_id']
        current_app.logger.info('%s recommender/ request with payload: %s', eri, input_json)

        try:
            check_license = request.args.get('check_license', 'false') == 'true'
            persist = request.args.get('persist', 'true') == 'true'
            r = handler.execute(input_json, persist=persist,
                                check_license=check_license)
        except Exception as e:
            r = {
                'recommendation': 'unexpected error',
                'external_request_id': input_json.get('external_request_id'),
                'message': '%s' % e
            }
            metrics_payload['status_code'] = 400
            current_app.logger.error('%s failed %s', eri, r)

    try:
        metrics_payload['value'] = get_time_delta(audit_data=r['result']['_audit'])
        push_data(metrics_payload)
    except KeyError:
        pass

    elapsed_secs = time.time() - recommender_started_at
    current_app.logger.info('%s took %0.2f seconds for _recommender', eri, elapsed_secs)

    return flask.jsonify(r), metrics_payload['status_code']


def _stack_aggregator(handler):
    eri = 'UNKNOWN'
    stack_aggregator_started_at = time.time()

    assert handler
    s = {'stack_aggregator': 'failure', 'external_request_id': None}
    input_json = request.get_json()
    # (fixme) Create decorator for metrics handling.
    metrics_payload = {
        'pid': os.getpid(),
        'hostname': os.environ.get("HOSTNAME"),
        'endpoint': request.endpoint,
        'request_method': request.method,
        'status_code': 200
    }

    if input_json and 'external_request_id' in input_json \
            and input_json['external_request_id']:
        eri = input_json['external_request_id']
        current_app.logger.info('%s stack_aggregator/ request with payload: %s', eri, input_json)

        try:
            persist = request.args.get('persist', 'true') == 'true'
            s = handler.execute(input_json, persist=persist)
            if s is not None and s.get('result') and s.get('result').get('_audit'):
                # Creating and Pushing Total Metrics Data to Accumulator
                metrics_payload['value'] = total_time_elapsed(
                    sa_audit_data=s['result']['_audit'],
                    external_request_id=input_json['external_request_id'])
                push_data(metrics_payload)

        except Exception as e:
            s = {
                'stack_aggregator': 'unexpected error',
                'external_request_id': input_json.get('external_request_id'),
                'message': '%s' % e
            }
            metrics_payload['status_code'] = 400
            current_app.logger.error('%s failed %s', eri, s)

        try:
            # Pushing Individual Metrics Data to Accumulator
            metrics_payload['value'] = get_time_delta(audit_data=s['result']['_audit'])
            metrics_payload['endpoint'] = request.endpoint
            push_data(metrics_payload)
        except KeyError:
            pass

    elapsed_secs = time.time() - stack_aggregator_started_at
    current_app.logger.info('%s took {t} seconds for _stack_aggregators', eri, elapsed_secs)

    return flask.jsonify(s)


@app.route('/api/v1/recommender', methods=['POST'])
def recommender_v1():
    """Handle POST requests that are sent to /api/v1/recommender REST API endpoint."""
    return _recommender(RecommendationTaskV1())


@app.route('/api/v1/stack_aggregator', methods=['POST'])
def stack_aggregator_v1():
    """Handle POST requests that are sent to /api/v1/stack_aggregator REST API endpoint."""
    return _stack_aggregator(StackAggregatorV1())


@app.route('/api/v2/recommender', methods=['POST'])
def recommender_v2():
    """Handle POST requests that are sent to /api/v2/recommender REST API endpoint."""
    return _recommender(RecommendationTaskV2())


@app.route('/api/v2/stack_aggregator', methods=['POST'])
def stack_aggregator_v2():
    """Handle POST requests that are sent to /api/v2/stack_aggregator REST API endpoint."""
    return _stack_aggregator(StackAggregatorV2())


if __name__ == "__main__":
    app.run()
