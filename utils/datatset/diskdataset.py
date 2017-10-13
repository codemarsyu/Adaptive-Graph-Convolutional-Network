from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import numpy as np
import tempfile
import time
import shutil
import pandas as pd
import random

from AGCN.utils.datatset import Dataset
from AGCN.utils.save import save_to_disk, load_from_disk, log


class DiskDataset(Dataset):
    """
    A Dataset that is stored as a set of files on disk.
    """

    def __init__(self, data_dir, verbose=True):
        """
        Turns featurized dataframes into numpy files, writes them & metadata to disk.
        """
        self.data_dir = data_dir
        self.verbose = verbose

        log("Loading dataset from disk.", self.verbose)
        if os.path.exists(self._get_metadata_filename()):
            (self.tasks,
             self.metadata_df) = load_from_disk(self._get_metadata_filename())
        else:
            raise ValueError("No metadata found on disk.")

    @staticmethod
    def create_dataset(shard_generator, data_dir=None, tasks=[], verbose=True):
        """Creates a new DiskDataset

        Parameters
        ----------
        shard_generator: Iterable
          An iterable (either a list or generator) that provides tuples of data
          (X, y, L, w, ids). Each tuple will be written to a separate shard on disk.
        data_dir: str
          Filename for data directory. Creates a temp directory if none specified.
        tasks: list
          List of tasks for this dataset.
        verbose:
        """
        if data_dir is None:
            data_dir = tempfile.mkdtemp()
        elif not os.path.exists(data_dir):
            os.makedirs(data_dir)

        metadata_rows = []
        time1 = time.time()
        for shard_num, (X, y, w, ids) in enumerate(shard_generator):
            basename = "shard-%d" % shard_num

            metadata_rows.append(
                DiskDataset.write_data_to_disk(data_dir, basename, tasks, X, y, w,
                                               ids))
        metadata_df = DiskDataset._construct_metadata(metadata_rows)
        metadata_filename = os.path.join(data_dir, "metadata.joblib")
        save_to_disk((tasks, metadata_df), metadata_filename)
        time2 = time.time()
        log("TIMING: dataset construction took %0.3f s" % (time2 - time1), verbose)
        return DiskDataset(data_dir, verbose=verbose)

    @staticmethod
    def _construct_metadata(metadata_entries):
        """Construct a dataframe containing metadata.

        metadata_entries should have elements returned by write_data_to_disk
        above.
        """
        columns = ('basename', 'task_names', 'ids', 'X', 'y', 'w')
        metadata_df = pd.DataFrame(metadata_entries, columns=columns)
        return metadata_df

    @staticmethod
    def write_data_to_disk(data_dir,
                           basename,
                           tasks,
                           X=None,
                           y=None,
                           w=None,
                           ids=None):
        if X is not None:
            out_X = "%s-X.joblib" % basename
            save_to_disk(X, os.path.join(data_dir, out_X))
        else:
            out_X = None

        if y is not None:
            out_y = "%s-y.joblib" % basename
            save_to_disk(y, os.path.join(data_dir, out_y))
        else:
            out_y = None

        if w is not None:
            out_w = "%s-w.joblib" % basename
            save_to_disk(w, os.path.join(data_dir, out_w))
        else:
            out_w = None

        if ids is not None:
            out_ids = "%s-ids.joblib" % basename
            save_to_disk(ids, os.path.join(data_dir, out_ids))
        else:
            out_ids = None

        # note that this corresponds to the _construct_metadata column order
        return [basename, tasks, out_ids, out_X, out_y, out_w]

    def save_to_disk(self):
        """Save dataset to disk."""
        save_to_disk((self.tasks, self.metadata_df), self._get_metadata_filename())

    def move(self, new_data_dir):
        """Moves dataset to new directory."""
        shutil.move(self.data_dir, new_data_dir)
        self.data_dir = new_data_dir

    def get_task_names(self):
        """
        Gets learning tasks associated with this dataset.
        """
        return self.tasks
        # if not len(self.metadata_df):
        #  raise ValueError("No data in dataset.")
        # return next(self.metadata_df.iterrows())[1]['task_names']

    def reshard(self, shard_size):
        """Reshards data to have specified shard size."""
        # Create temp directory to store resharded version
        reshard_dir = tempfile.mkdtemp()
        new_metadata = []

        # Write data in new shards
        def generator():
            tasks = self.get_task_names()
            X_next = np.zeros((0,) + self.get_data_shape())
            y_next = np.zeros((0,) + (len(tasks),))
            w_next = np.zeros((0,) + (len(tasks),))
            ids_next = np.zeros((0,), dtype=object)
            for (X, y, w, ids) in self.itershards():
                X_next = np.vstack([X_next, X])
                y_next = np.vstack([y_next, y])
                w_next = np.vstack([w_next, w])
                ids_next = np.concatenate([ids_next, ids])
                while len(X_next) > shard_size:
                    X_batch, X_next = X_next[:shard_size], X_next[shard_size:]
                    y_batch, y_next = y_next[:shard_size], y_next[shard_size:]
                    w_batch, w_next = w_next[:shard_size], w_next[shard_size:]
                    ids_batch, ids_next = ids_next[:shard_size], ids_next[shard_size:]
                    yield (X_batch, y_batch, w_batch, ids_batch)
            # Handle spillover from last shard
            yield (X_next, y_next, w_next, ids_next)

        resharded_dataset = DiskDataset.create_dataset(
            generator(), data_dir=reshard_dir, tasks=self.tasks)
        shutil.rmtree(self.data_dir)
        shutil.move(reshard_dir, self.data_dir)
        self.metadata_df = resharded_dataset.metadata_df
        self.save_to_disk()

    def get_data_shape(self):
        """
        Gets array shape of datapoints in this dataset.
        """
        if not len(self.metadata_df):
            raise ValueError("No data in dataset.")
        sample_X = load_from_disk(
            os.path.join(self.data_dir, next(self.metadata_df.iterrows())[1]['X']))[
            0]
        return np.shape(sample_X)

    def get_shard_size(self):
        """Gets size of shards on disk."""
        if not len(self.metadata_df):
            raise ValueError("No data in dataset.")
        sample_y = load_from_disk(
            os.path.join(self.data_dir, next(self.metadata_df.iterrows())[1]['y']))
        return len(sample_y)

    def _get_metadata_filename(self):
        """
        Get standard location for metadata file.
        """
        metadata_filename = os.path.join(self.data_dir, "metadata.joblib")
        return metadata_filename

    def get_number_shards(self):
        """
        Returns the number of shards for this dataset.
        """
        return self.metadata_df.shape[0]

    def itershards(self):
        """
        Return an object that iterates over all shards in dataset.

        Datasets are stored in sharded fashion on disk. Each call to next() for the
        generator defined by this function returns the data from a particular shard.
        The order of shards returned is guaranteed to remain fixed.
        """

        def iterate(dataset):
            for _, row in dataset.metadata_df.iterrows():
                X = np.array(load_from_disk(os.path.join(dataset.data_dir, row['X'])))
                ids = np.array(
                    load_from_disk(os.path.join(dataset.data_dir, row['ids'])),
                    dtype=object)
                # These columns may be missing is the dataset is unlabelled.
                if row['y'] is not None:
                    y = np.array(load_from_disk(os.path.join(dataset.data_dir, row['y'])))
                else:
                    y = None
                if row['w'] is not None:
                    w_filename = os.path.join(dataset.data_dir, row['w'])
                    if os.path.exists(w_filename):
                        w = np.array(load_from_disk(w_filename))
                    else:
                        w = np.ones(y.shape)
                else:
                    w = None
                yield (X, y, w, ids)

        return iterate(self)

    def iterbatches(self,
                    batch_size=None,
                    epoch=0,
                    deterministic=False,
                    pad_batches=False):
        """Get an object that iterates over minibatches from the dataset.
        Each minibatch is returned as a tuple of four numpy arrays: (X, y, w, ids).
        """

        def iterate(dataset):
            num_shards = dataset.get_number_shards()
            if not deterministic:
                shard_perm = np.random.permutation(num_shards)
            else:
                shard_perm = np.arange(num_shards)
            for i in range(num_shards):
                X, y, w, ids = dataset.get_shard(shard_perm[i])
                n_samples = X.shape[0]
                # TODO(rbharath): This happens in tests sometimes, but don't understand why?
                # Handle edge case.
                if n_samples == 0:
                    continue
                if not deterministic:
                    sample_perm = np.random.permutation(n_samples)
                else:
                    sample_perm = np.arange(n_samples)
                if batch_size is None:
                    shard_batch_size = n_samples
                else:
                    shard_batch_size = batch_size
                interval_points = np.linspace(
                    0,
                    n_samples,
                    np.ceil(float(n_samples) / shard_batch_size) + 1,
                    dtype=int)
                for j in range(len(interval_points) - 1):
                    indices = range(interval_points[j], interval_points[j + 1])
                    perm_indices = sample_perm[indices]
                    X_batch = X[perm_indices]

                    if y is not None:
                        y_batch = y[perm_indices]
                    else:
                        y_batch = None

                    if w is not None:
                        w_batch = w[perm_indices]
                    else:
                        w_batch = None

                    ids_batch = ids[perm_indices]
                    if pad_batches:
                        (X_batch, y_batch, w_batch, ids_batch) = Dataset.pad_batch(
                            shard_batch_size, X_batch, y_batch, w_batch, ids_batch)
                    yield (X_batch, y_batch, w_batch, ids_batch)

        return iterate(self)

    def itersamples(self):
        """Get an object that iterates over the samples in the dataset.

        Example:

        >>> dataset = DiskDataset.from_numpy(np.ones((2,2)), np.ones((2,1)), verbose=False)
        >>> for x, y, w, id in dataset.itersamples():
        ...   print(x, y, w, id)
        [ 1.  1.] [ 1.] [ 1.] 0
        [ 1.  1.] [ 1.] [ 1.] 1
        """

        def iterate(dataset):
            for (X_shard, y_shard, w_shard, ids_shard) in dataset.itershards():
                n_samples = X_shard.shape[0]
                for i in range(n_samples):

                    def sanitize(elem):
                        if elem is None:
                            return None
                        else:
                            return elem[i]

                    yield map(sanitize, [X_shard, y_shard, w_shard, ids_shard])

        return iterate(self)

    def transform(self, fn, **args):
        """Construct a new dataset by applying a transformation to every sample in this dataset.

        The argument is a function that can be called as follows:

        >> newx, newy, neww = fn(x, y, w)

        It might be called only once with the whole dataset, or multiple times with different
        subsets of the data.  Each time it is called, it should transform the samples and return
        the transformed data.

        Parameters
        ----------
        fn: function
          A function to apply to each sample in the dataset
        out_dir: string
          The directory to save the new dataset in.  If this is omitted, a temporary directory
          is created automatically

        Returns
        -------
        a newly constructed Dataset object
        """
        if 'out_dir' in args:
            out_dir = args['out_dir']
        else:
            out_dir = tempfile.mkdtemp()
        tasks = self.get_task_names()

        def generator():
            for shard_num, row in self.metadata_df.iterrows():
                X, y, w, ids = self.get_shard(shard_num)
                newx, newy, neww = fn(X, y, w)
                yield (newx, newy, neww, ids)

        return DiskDataset.create_dataset(
            generator(), data_dir=out_dir, tasks=tasks)

    @staticmethod
    def from_numpy(X,
                   y,
                   w=None,
                   ids=None,
                   tasks=None,
                   data_dir=None,
                   verbose=True):
        """Creates a DiskDataset object from specified Numpy arrays."""
        # if data_dir is None:
        #  data_dir = tempfile.mkdtemp()
        n_samples = len(X)
        # The -1 indicates that y will be reshaped to have length -1
        if n_samples > 0:
            y = np.reshape(y, (n_samples, -1))
            if w is not None:
                w = np.reshape(w, (n_samples, -1))
        n_tasks = y.shape[1]
        if ids is None:
            ids = np.arange(n_samples)
        if w is None:
            w = np.ones_like(y)
        if tasks is None:
            tasks = np.arange(n_tasks)
        # raw_data = (X, y, w, ids)
        return DiskDataset.create_dataset(
            [(X, y, w, ids)], data_dir=data_dir, tasks=tasks, verbose=verbose)

    @staticmethod
    def merge(datasets, merge_dir=None):
        """Merges provided datasets into a merged dataset."""
        if merge_dir is not None:
            if not os.path.exists(merge_dir):
                os.makedirs(merge_dir)
        else:
            merge_dir = tempfile.mkdtemp()

        def generator():
            for ind, dataset in enumerate(datasets):
                X, y, w, ids = (dataset.X, dataset.y, dataset.w, dataset.ids)
                yield (X, y, w, ids)

        return DiskDataset.create_dataset(generator(), data_dir=merge_dir)

    def subset(self, shard_nums, subset_dir=None):
        """Creates a subset of the original dataset on disk."""
        if subset_dir is not None:
            if not os.path.exists(subset_dir):
                os.makedirs(subset_dir)
        else:
            subset_dir = tempfile.mkdtemp()
        tasks = self.get_task_names()

        def generator():
            for shard_num, row in self.metadata_df.iterrows():
                if shard_num not in shard_nums:
                    continue
                X, y, w, ids = self.get_shard(shard_num)
                yield (X, y, w, ids)

        return DiskDataset.create_dataset(
            generator(), data_dir=subset_dir, tasks=tasks)

    def sparse_shuffle(self):
        """Shuffling that exploits data sparsity to shuffle large datasets.

        Only for 1-dimensional feature vectors (does not work for tensorial
        featurizations).
        """
        time1 = time.time()
        shard_size = self.get_shard_size()
        num_shards = self.get_number_shards()
        X_sparses, ys, ws, ids = [], [], [], []
        num_features = None
        for i in range(num_shards):
            (X_s, y_s, w_s, ids_s) = self.get_shard(i)
            if num_features is None:
                num_features = X_s.shape[1]
            X_sparse = Dataset.sparsify_features(X_s)
            X_sparses, ys, ws, ids = (X_sparses + [X_sparse], ys + [y_s], ws + [w_s],
                                      ids + [np.atleast_1d(np.squeeze(ids_s))])
        # Get full dataset in memory
        (X_sparse, y, w, ids) = (np.vstack(X_sparses), np.vstack(ys), np.vstack(ws),
                                 np.concatenate(ids))
        # Shuffle in memory
        num_samples = len(X_sparse)
        permutation = np.random.permutation(num_samples)
        X_sparse, y, w, ids = (X_sparse[permutation], y[permutation],
                               w[permutation], ids[permutation])
        # Write shuffled shards out to disk
        for i in range(num_shards):
            start, stop = i * shard_size, (i + 1) * shard_size
            (X_sparse_s, y_s, w_s, ids_s) = (X_sparse[start:stop], y[start:stop],
                                             w[start:stop], ids[start:stop])
            X_s = Dataset.densify_features(X_sparse_s, num_features)
            self.set_shard(i, X_s, y_s, w_s, ids_s)
        time2 = time.time()
        log("TIMING: sparse_shuffle took %0.3f s" % (time2 - time1), self.verbose)

    def shuffle_each_shard(self):
        """Shuffles elements within each shard of the datset."""
        tasks = self.get_task_names()
        # Shuffle the arrays corresponding to each row in metadata_df
        n_rows = len(self.metadata_df.index)
        n_rows = len(self.metadata_df.index)
        for i in range(n_rows):
            row = self.metadata_df.iloc[i]
            basename = row["basename"]
            X, y, w, ids = self.get_shard(i)
            n = X.shape[0]
            permutation = np.random.permutation(n)
            X, y, w, ids = (X[permutation], y[permutation], w[permutation],
                            ids[permutation])
            DiskDataset.write_data_to_disk(self.data_dir, basename, tasks, X, y, w,
                                           ids)

    def shuffle_shards(self):
        """Shuffles the order of the shards for this dataset."""
        metadata_rows = self.metadata_df.values.tolist()
        random.shuffle(metadata_rows)
        self.metadata_df = DiskDataset._construct_metadata(metadata_rows)
        self.save_to_disk()

    def get_shard(self, i):
        """Retrieves data for the i-th shard from disk."""
        row = self.metadata_df.iloc[i]
        X = np.array(load_from_disk(os.path.join(self.data_dir, row['X'])))

        if row['y'] is not None:
            y = np.array(load_from_disk(os.path.join(self.data_dir, row['y'])))
        else:
            y = None

        if row['w'] is not None:
            # TODO (ytz): Under what condition does this exist but the file itself doesn't?
            w_filename = os.path.join(self.data_dir, row['w'])
            if os.path.exists(w_filename):
                w = np.array(load_from_disk(w_filename))
            else:
                w = np.ones(y.shape)
        else:
            w = None

        ids = np.array(
            load_from_disk(os.path.join(self.data_dir, row['ids'])), dtype=object)
        return X, y, w, ids

    def add_shard(self, X, y, w, ids):
        """Adds a data shard."""
        metadata_rows = self.metadata_df.values.tolist()
        shard_num = len(metadata_rows)
        basename = "shard-%d" % shard_num
        tasks = self.get_task_names()
        metadata_rows.append(
            DiskDataset.write_data_to_disk(self.data_dir, basename, tasks, X, y, w,
                                           ids))
        self.metadata_df = DiskDataset._construct_metadata(metadata_rows)
        self.save_to_disk()

    def set_shard(self, shard_num, X, y, w, ids):
        """Writes data shard to disk"""
        basename = "shard-%d" % shard_num
        tasks = self.get_task_names()
        DiskDataset.write_data_to_disk(self.data_dir, basename, tasks, X, y, w, ids)

    def select(self, indices, select_dir=None):
        """Creates a new dataset from a selection of indices from self.

        Parameters
        ----------
        select_dir: string
          Path to new directory that the selected indices will be copied to.
        indices: list
          List of indices to select.
        """
        if select_dir is not None:
            if not os.path.exists(select_dir):
                os.makedirs(select_dir)
        else:
            select_dir = tempfile.mkdtemp()
        # Handle edge case with empty indices
        if not len(indices):
            return DiskDataset.create_dataset(
                [], data_dir=select_dir, verbose=self.verbose)
        indices = np.array(sorted(indices)).astype(int)
        tasks = self.get_task_names()

        def generator():
            count, indices_count = 0, 0
            for shard_num, (X, y, w, ids) in enumerate(self.itershards()):
                shard_len = len(X)
                # Find indices which rest in this shard
                num_shard_elts = 0
                while indices[indices_count + num_shard_elts] < count + shard_len:
                    num_shard_elts += 1
                    if indices_count + num_shard_elts >= len(indices):
                        break
                # Need to offset indices to fit within shard_size
                shard_inds = indices[indices_count:indices_count +
                                                   num_shard_elts] - count
                X_sel = X[shard_inds]
                y_sel = y[shard_inds]
                w_sel = w[shard_inds]
                ids_sel = ids[shard_inds]
                yield (X_sel, y_sel, w_sel, ids_sel)
                # Updating counts
                indices_count += num_shard_elts
                count += shard_len
                # Break when all indices have been used up already
                if indices_count >= len(indices):
                    return

        return DiskDataset.create_dataset(
            generator(), data_dir=select_dir, tasks=tasks, verbose=self.verbose)

    @property
    def ids(self):
        """Get the ids vector for this dataset as a single numpy array."""
        if len(self) == 0:
            return np.array([])
        ids = []
        for (_, _, _, ids_b) in self.itershards():
            ids.append(np.atleast_1d(np.squeeze(ids_b)))
        return np.concatenate(ids)

    @property
    def X(self):
        """Get the X vector for this dataset as a single numpy array."""
        Xs = []
        one_dimensional = False
        for (X_b, _, _, _) in self.itershards():
            Xs.append(X_b)
            if len(X_b.shape) == 1:
                one_dimensional = True
        if not one_dimensional:
            return np.vstack(Xs)
        else:
            return np.concatenate(Xs)

    @property
    def y(self):
        """Get the y vector for this dataset as a single numpy array."""
        ys = []
        for (_, y_b, _, _) in self.itershards():
            ys.append(y_b)
        return np.vstack(ys)

    @property
    def w(self):
        """Get the weight vector for this dataset as a single numpy array."""
        ws = []
        for (_, _, w_b, _) in self.itershards():
            ws.append(np.array(w_b))
        return np.vstack(ws)

    def __len__(self):
        """
        Finds number of elements in dataset.
        """
        total = 0
        for _, row in self.metadata_df.iterrows():
            y = load_from_disk(os.path.join(self.data_dir, row['ids']))
            total += len(y)
        return total

    def get_shape(self):
        """Finds shape of dataset."""
        n_tasks = len(self.get_task_names())
        X_shape = np.array((0,) + (0,) * len(self.get_data_shape()))
        ids_shape = np.array((0,))
        if n_tasks > 0:
            y_shape = np.array((0,) + (0,))
            w_shape = np.array((0,) + (0,))
        else:
            y_shape = tuple()
            w_shape = tuple()

        for shard_num, (X, y, w, ids) in enumerate(self.itershards()):
            if shard_num == 0:
                X_shape += np.array(X.shape)
                if n_tasks > 0:
                    y_shape += np.array(y.shape)
                    w_shape += np.array(w.shape)
                ids_shape += np.array(ids.shape)
            else:
                X_shape[0] += np.array(X.shape)[0]
                if n_tasks > 0:
                    y_shape[0] += np.array(y.shape)[0]
                    w_shape[0] += np.array(w.shape)[0]
                ids_shape[0] += np.array(ids.shape)[0]
        return tuple(X_shape), tuple(y_shape), tuple(w_shape), tuple(ids_shape)

    def get_label_means(self):
        """Return pandas series of label means."""
        return self.metadata_df["y_means"]

    def get_label_stds(self):
        """Return pandas series of label stds."""
        return self.metadata_df["y_stds"]
