#change
test_job = execute(
    qc,
    qobj_header={
        'test_header': 'test'
    },
    backend=backend
)

test_job.result().header.to_dict()

#to
qc.metadata = {'test_header': 'test'}
test_job = execute(qc, backend=backend)