#!/usr/bin/env python


from __future__ import print_function, division

import os
import argparse
from time import strftime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from neuralnilm.data.processing import DivideBy, IndependentlyCenter

from lib import dirs


# Parameters
INPUT_FILENAME = None
TARGET_APPLIANCE = None
SAMPLE_PERIOD = None
VALID_TARGET_FILENAME = None
STRIDE = None


# Main
def main():
    global STRIDE

    parse_args()

    # load data
    print('Loading data ...')
    data = pd.read_csv(INPUT_FILENAME,
                       parse_dates=['reporttime'],
                       index_col='reporttime')[['w']].resample('{}S'.format(SAMPLE_PERIOD)).max().ffill().sort_index()
    data_values = data['w'].values

    # load the model
    print('Loading model ...')
    model_filename = os.path.join(dirs.MODELS_DIR, TARGET_APPLIANCE + '.h5')
    from keras.models import load_model
    model = load_model(model_filename)

    # determine sequence length, and set STRIDE to sequence length if it's not
    # specified by user
    print('Determining sequence length ... ', end='')
    seq_length = model.input_shape[1]
    print(seq_length)
    if STRIDE is None:
        print('STRIDE wasn\'t specified; setting it to {} ...'.format(seq_length))
        STRIDE = seq_length

    # pad the input if its length minus sequence length isn't a multiple of STRIDE
    original_data_len = data.shape[0]
    tmp = (data.shape[0] - seq_length) % STRIDE
    if tmp != 0:
        print('Padding data ...')
        data = data.append(pd.DataFrame(0,
                                        index=pd.date_range(str(data.index[-1] + 1), periods=(STRIDE - tmp), freq='{}S'.format(SAMPLE_PERIOD)),
                                        columns=['w']))

    # load processing parameters
    print('Loading processing parameters ...')
    proc_params_filename = os.path.join(dirs.MODELS_DIR, 'proc_params_' + TARGET_APPLIANCE + '.npz')
    input_std, target_std = np.load(proc_params_filename)['arr_0']

    # prepare input for prediction and then predict it
    print('Preparing input for prediction ...')
    prediction_input = []
    for i in range(0, data.shape[0] - seq_length + 1, STRIDE):
        prediction_input.append(data.values[i:i+seq_length])
    prediction_input = IndependentlyCenter()(DivideBy(input_std)(np.array(prediction_input)))
    print('Predicting ...')
    prediction_output = model.predict(prediction_input)

    # deal with the overlaps
    print('Dealing with overlaps ...')
    combined_prediction_output = np.zeros(data.shape[0])
    overlapping_count = np.zeros(data.shape[0], dtype=int)
    for i, pred in enumerate(prediction_output):
        start_index, end_index = (i*STRIDE, i*STRIDE+seq_length)
        combined_prediction_output[start_index:end_index] += pred.flatten()
        overlapping_count[start_index:end_index] += 1
    combined_prediction_output /= overlapping_count

    # cut the lengths down to original lengths before padding
    data = data.iloc[:original_data_len]
    combined_prediction_output = combined_prediction_output[:original_data_len]

    # apply inverse processing to prediction
    target_processing = DivideBy(target_std)
    combined_prediction_output = target_processing.inverse(combined_prediction_output)

    # output
    print('Creating output directory ... ', end='')
    output_dir = os.path.join(dirs.PREDICTION_OUTPUT_DIR,
                              os.path.splitext(os.path.basename(INPUT_FILENAME))[0] + '_' + TARGET_APPLIANCE + '_' + strftime('%Y-%m-%d_%H-%M-%S'))
    os.makedirs(output_dir)
    print(output_dir)
    print('Generating prediction.csv ...')
    data['prediction'] = combined_prediction_output
    data.to_csv(os.path.join(output_dir, 'prediction.csv'), columns=['prediction'])
    print('Generating prediction.png ...')
    p1 = plt.subplot(121)
    p1.set_title('Input')
    p2 = plt.subplot(122, sharey=p1)
    p2.set_title('Prediction')
    p1.plot(data_values)
    p2.plot(combined_prediction_output)
    plt.savefig(os.path.join(output_dir, 'prediction.png'))

    # perform validation (if asked to)
    if VALID_TARGET_FILENAME:
        valid_target = pd.read_csv(VALID_TARGET_FILENAME,
                                   parse_dates=['reporttime'],
                                   index_col='reporttime')[['w']].resample('{}S'.format(SAMPLE_PERIOD)).max().ffill().sort_index()
        valid_target_values = valid_target['w'].values
        assert(valid_target_values.shape[0] == combined_prediction_output.shape[0])

        # define metrics
        def mean_squared_error(y_true, y_pred):
            return ((y_true - y_pred)**2).mean()
        def mean_absolute_error(y_true, y_pred):
            return (np.abs(y_true - y_pred)).mean()
        ON_POWER_THRESHOLD = target_processing(10)
        def acc(y_true, y_pred):
            return np.mean((y_true >= ON_POWER_THRESHOLD) == (y_pred >= ON_POWER_THRESHOLD))
        metrics = [mean_squared_error, mean_absolute_error, acc]

        print('Validation metrics: ', end='')
        log = pd.DataFrame()
        for m in metrics:
            m_name = m.func_name
            m_value = m(target_processing(valid_target_values), target_processing(combined_prediction_output))
            log[m_name] = [m_value]
            print('{}={:.4f}, '.format(m_name, m_value), end='')
        print('')
        print('Writing log ...')
        log.to_csv(os.path.join(output_dir, 'log.csv'), index=False, float_format='%.4f')
        print('')

        print('Generating validation.png ...')
        p1 = plt.subplot(131)
        p1.set_title('Input')
        p2 = plt.subplot(132, sharey=p1)
        p2.set_title('Target')
        p3 = plt.subplot(133, sharey=p1)
        p3.set_title('Prediction')
        p1.plot(data_values)
        p2.plot(valid_target_values)
        p3.plot(combined_prediction_output)
        plt.savefig(os.path.join(output_dir, 'validation.png'))


# Argument parser
def parse_args():
    global INPUT_FILENAME, TARGET_APPLIANCE, SAMPLE_PERIOD, STRIDE, VALID_TARGET_FILENAME

    parser = argparse.ArgumentParser()

    # required
    required_named_arguments = parser.add_argument_group('required named arguments')
    required_named_arguments.add_argument('-d', '--input-filename',
                                          help='Input\'s filename (csv).',
                                          required=True)
    required_named_arguments.add_argument('-a', '--target-appliance',
                                          help='Target appliance. For example, \'fridge\'.',
                                          required=True)
    required_named_arguments.add_argument('-s', '--sample-period',
                                          help='Sample period (in seconds).',
                                          type=int,
                                          required=True)

    # optional
    optional_named_arguments = parser.add_argument_group('optional named arguments')
    optional_named_arguments.add_argument('-t', '--stride',
                                          help='Stride. If not given, it will be set to sequence length.',
                                          type=int)
    optional_named_arguments.add_argument('-v', '--valid-target-filename',
                                          help='Validation target\'s filename (csv). If not given, no validation will be performed.')

    # start parsing
    args = parser.parse_args()

    INPUT_FILENAME = args.input_filename
    TARGET_APPLIANCE = args.target_appliance
    SAMPLE_PERIOD = args.sample_period

    STRIDE = args.stride
    VALID_TARGET_FILENAME = args.valid_target_filename


if __name__ == '__main__':
    main()
