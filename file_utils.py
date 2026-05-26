import os, yaml, scipy.io, pickle, csv
from PyQt6 import QtGui, QtWidgets


def dict2yaml(expt_dict, filename='exp_params.yaml'):
    with open(filename, 'w') as outfile:
        outfile.write(yaml.dump(expt_dict, default_flow_style=False))


def yaml2dict(filename='exp_params.yaml'):

    data = {}
    with open(filename, 'r') as infile:
        try:
            data = yaml.safe_load(infile)
        except yaml.YAMLError as exc:
            print(exc)

    if data != {}:
        return data
    else:
        raise ImportError


def save_data(filename, data, graph=None, fig=None, sweep_params=None):
    if True:
        scipy.io.savemat(os.path.expanduser(os.path.join('~', 'Documents', 'data_mat', filename+'.mat')), mdict=data)
    if graph is not None:
        graph.save(os.path.expanduser(os.path.join('~', 'Documents', 'data_screenshots', '%s.png' % filename)), 'png')
    if fig is not None:
        fig.save(os.path.expanduser(os.path.join('~', 'Documents', 'data_images', '%s.png' % filename)), 'png')
    if sweep_params is not None:
        dict2yaml(sweep_params, os.path.expanduser(os.path.join('~', 'Documents', 'data_mat', '%s.yaml' % filename)))


def save_csv(filename, metrics, template_fields, mainexp=None):
    """
    Processes template fields, resolves values from metrics or PyQt widgets on
    the mainexp instance, and writes the compiled rows to a CSV.

    Parameters:
    -----------
    filename : str
        The output path of the CSV file.
    metrics : dict
        A dictionary containing key-value pairs of calculated metrics.
    template_fields : list of tuple
        The cached (field_name, alt_field_name) tuples loaded at startup.
    mainexp : QWidget/QMainWindow, optional
        The instance of your main application (usually passed as 'self')
        to retrieve widget and parameter values from.
    """
    processed_rows = []

    for field_name, alt_field_name in template_fields:

        # --- Rule 1: Decorators starting with '-' ---
        if field_name.startswith('-'):
            processed_rows.append([field_name, ""])
            continue

        resolved_value = None
        found = False

        # --- Rule 2: Check metrics dictionary first ---
        if field_name in metrics:
            resolved_value = metrics[field_name]
            found = True

        # --- Rule 3: Check widgets/variables on the mainexp instance ---
        elif alt_field_name and mainexp is not None:
            if hasattr(mainexp, alt_field_name):
                var_obj = getattr(mainexp, alt_field_name)

                # Dynamically extract values depending on widget type
                if hasattr(var_obj, 'value') and callable(var_obj.value):
                    resolved_value = var_obj.value()
                    found = True
                elif hasattr(var_obj, 'text') and callable(var_obj.text):
                    resolved_value = var_obj.text()
                    found = True
                elif hasattr(var_obj, 'isChecked') and callable(var_obj.isChecked):
                    resolved_value = var_obj.isChecked()
                    found = True
                elif not callable(var_obj):
                    resolved_value = var_obj
                    found = True

        # --- Rule 4: Fallback to 'n/a' if not resolved ---
        if not found or resolved_value is None:
            resolved_value = "n/a"

        processed_rows.append([field_name, resolved_value])

    # --- Write final compiled rows to the CSV ---
    try:
        fullfilename = os.path.expanduser(os.path.join('~', 'Documents', 'data_mat', '%s.csv' % filename))
        with open(fullfilename, mode='w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)
            writer.writerows(processed_rows)
    except Exception as e:
        print(f"Error writing to CSV: {e}")

def savemat(path, data):
    scipy.io.savemat(path, mdict=data)


def getwavenum():
    eppath = os.path.expanduser(os.path.join('~', 'Documents', 'data_mat'))
    filelist = [f for f in os.listdir(eppath) if os.path.isfile(os.path.join(eppath, f))]

    numlist = []
    for f in filelist:
        if '.mat' in f:
            filename = f.split('.mat')[0]
            num = filename.split('_')[1]
            if num.isdigit(): numlist.append(int(num))

    if not numlist:
        return 0
    else:
        return max(numlist)


def save_config(configsettings, path='.'):
    '''save gui settings which are passed in as dictionary, configsettings'''
    filename = 'guisettings.config'
    fullpath = os.path.join(path, filename)
    pickle.dump(configsettings, open(fullpath, 'wb'))


def load_config(fullpath='./guisettings.config'):
    '''load settings for the gui and populate it'''
    data = pickle.load(open(fullpath,'rb'))
    return data


def table2csv(qtable, filename):
    numrows = qtable.rowCount()

    with open(filename, 'wt', newline='') as csvfile:
        writer = csv.writer(csvfile)
        for row in range(numrows):
            row2write = []
            for col in range(qtable.columnCount()):
                item = qtable.item(row, col)
                if item is None:
                    row2write.append('')
                else:
                    row2write.append(item.text())

            writer.writerow(iter(row2write))


def csv2table(qtable, filename):
    with open(filename, 'rt', newline='') as csvfile:
        reader = csv.reader(csvfile)

        table = [data for data in reader]

        numrows = len(table)
        qtable.setRowCount(numrows)

        for row in range(len(table)):
            for col in range(len(table[row])):
                qtable.setItem(row, col, QtWidgets.QTableWidgetItem(table[row][col]))
