# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simple web app for storing PSI results in BigQuery."""
from datetime import datetime
import logging
import os

import flask
from google.cloud import bigquery
import google.cloud.logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import typing

app = flask.Flask(__name__)

logging_client = google.cloud.logging.Client()
logging_client.get_default_handler()
logging_client.setup_logging()


def get_psi_audit(test_url: str) -> dict:
  """Requsts an audit from PSI for the given URL.

  Sends a request to Pagespeed Insights for a performance audit with the mobile
  profile to Pagespeed Insights.

  Args:
    test_url (string): the URL to audit.

  Returns:
    The resutl of the audit in a python object.
  """
  psi_response = None
  with build('pagespeedonline', 'v5') as g_service:
    psi_service = g_service.pagespeedapi()
    psi_request = psi_service.runpagespeed(
        url=test_url,
        category='PERFORMANCE',
        strategy='MOBILE')
    try:
      psi_response = psi_request.execute()
    except HttpError as he:
      logging.exception('PSI Error %d: %s', he.status_code, he.error_details)
    finally:
      psi_service.close()

  return  typing.cast(dict, psi_response)


def extract_audits(psi_json: dict) -> dict[str, float]:
  """Extracts the relevant parts of the Lighthouse audit.

  Takes the PSI result as a dict and places the relevant audit values into a
  simple dict with keys ready for bigquery.

  Args:
    psi_json (dict): The JSON reponse from PSI parsed into a dict.

  Returns:
    Only the relevant audit scores in a dict.
  """
  audits = {
      'speed-index': 'speed_index',
      'first-contentful-paint': 'first_contentful_paint',
      'first-meaningful-paint': 'first_meaningful_paint',
      'server-response-time': 'server_response_time',
      'network-server-latency': 'network_server_latency',
      'cumulative-layout-shift': 'cumulative_layout_shift',
      'interactive': 'interactive',
      'largest-contentful-paint': 'largest_contentful_paint',
      'total-blocking-time': 'total_blocking_time',
      'first-cpu-idle': 'first_cpu_idle',
      'max-potential-fid': 'max_potential_fid',
      'total-byte-weight': 'total_byte_weight',
      'estimated-input-latency': 'estimated_input_latency',
  }

  to_return = {}
  to_return['date'] = datetime.strptime(
      psi_json['analysisUTCTimestamp'], '%Y-%m-%dT%H:%M:%S.%fZ')
  to_return['url'] = psi_json['lighthouseResult']['finalUrl']
  for a in audits:
    to_return[audits[a]] = psi_json['lighthouseResult']['audits'][a]['numericValue']

  return to_return


def insert_audits(table: str, audits: dict[str, float]) -> None:
  """Inserts given audit results into bigquery.

  The given audit dict is inserted into the given bigquery table.

  Args:
    table (str): the dataset and table name to insert the data into.
    audits (dict[str, float]): the audit results to insert into the table.

  Raises:
    RuntimeError: If the write to bigquery fails.
  """
  bq_client = bigquery.Client()
  project_name = os.environ['GOOGLE_CLOUD_PROJECT']
  bq_table = bq_client.get_table(f'{project_name}.{table}')
  errors = bq_client.insert_rows(bq_table, [audits])
  if errors:
    raise RuntimeError(f'Failed to write all rows to bigquery: {errors}.')


@app.route('/')
def index_page():
  """Serve the index page to allow people to submit requests."""
  return flask.render_template('index.html', state=None)


@app.route('/submit', methods=['POST'])
def submit_test():
  """Submits a request to Pagespeed Insights."""
  test_url = flask.request.form['test_url']
  psi_object = get_psi_audit(test_url)
  audits = extract_audits(psi_object)
  bq_table = os.environ['BIGQUERY_TABLE']
  try:
    insert_audits(bq_table, audits)
  except (ValueError, RuntimeError) as err:
    logging.exception(err)
    return flask.render_template('index.html', state='failure')

  return flask.render_template('index.html', state='success')
