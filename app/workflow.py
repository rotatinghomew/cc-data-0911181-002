from . import planning
from . import funding
from . import resources

OPERATIONS={
    'planning.nominate':planning.nominate,
    'funding.approve':funding.approve,
    'resources.activate':resources.activate,
}
