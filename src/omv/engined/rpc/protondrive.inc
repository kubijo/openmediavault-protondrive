<?php

/** GPL-3.0-or-later. Based on OMV's OneDrive and Rsync RPC services. */

namespace Engined\Rpc;

class ProtonDrive extends \OMV\Rpc\ServiceAbstract
{
    public function getName()
    {
        return 'ProtonDrive';
    }

    public function initialize()
    {
        foreach ([
            'get',
            'set',
            'getSetList',
            'getSet',
            'setSet',
            'deleteSet',
            'getStatus',
            'startAuth',
            'getAuthStatus',
            'cancelAuth',
            'logout',
            'runNow',
            'cancelRun',
            'recoverContainers',
        ] as $method) {
            $this->registerMethod($method);
        }
    }

    private function admin($context)
    {
        $this->validateMethodContext($context, ['role' => OMV_ROLE_ADMINISTRATOR]);
    }

    private function command($operation)
    {
        $cmd = new \OMV\System\Process('/usr/sbin/omv-protondrive', [$operation]);
        $cmd->setRedirect2to1();
        $cmd->execute($output, $exitCode);
        if ($exitCode !== 0) {
            throw new \OMV\ExecException($cmd, $output, $exitCode);
        }
        return json_decode(implode("\n", $output), true);
    }

    private function validateCandidate($global, $sets)
    {
        $global['sets'] = $sets;
        $process = proc_open(
            ['/usr/sbin/omv-protondrive', 'validate-stdin'],
            [['pipe', 'r'], ['pipe', 'w'], ['pipe', 'w']],
            $pipes,
        );
        if (!is_resource($process)) {
            throw new \RuntimeException('Could not validate backup configuration');
        }
        fwrite($pipes[0], json_encode($global));
        fclose($pipes[0]);
        stream_get_contents($pipes[1]);
        fclose($pipes[1]);
        $error = stream_get_contents($pipes[2]);
        fclose($pipes[2]);
        if (proc_close($process) !== 0) {
            throw new \RuntimeException(trim($error));
        }
    }

    private function allSets()
    {
        $db = \OMV\Config\Database::getInstance();
        return array_map(function ($object) {
            return $object->getAssoc();
        }, $db->get('conf.service.protondrive.set'));
    }

    public function get($params, $context)
    {
        $this->admin($context);
        $settings = \OMV\Config\Database::getInstance()->get('conf.service.protondrive')->getAssoc();
        unset($settings['sets']);
        return $settings;
    }

    public function set($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.protondrive.set');
        $this->command('check-idle');
        if ($params['instanceuuid'] !== $this->get([], $context)['instanceuuid']) {
            throw new \RuntimeException('The instance UUID cannot be changed');
        }
        $this->validateCandidate($params, $this->allSets());
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get('conf.service.protondrive');
        $object->setAssoc($params);
        $db->set($object);
        return $this->get([], $context);
    }

    public function getSetList($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.common.getlist');
        return $this->applyFilter(
            $this->allSets(),
            $params['start'],
            $params['limit'],
            $params['sortfield'],
            $params['sortdir'],
        );
    }

    public function getSet($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.common.objectuuid');
        return \OMV\Config\Database::getInstance()->get('conf.service.protondrive.set', $params['uuid'])->getAssoc();
    }

    public function setSet($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.protondrive.setSet');
        $this->command('check-idle');
        $object = new \OMV\Config\ConfigObject('conf.service.protondrive.set');
        $object->setAssoc($params, true, true);
        $candidate = $object->getAssoc();
        $sets = [];
        foreach ($this->allSets() as $item) {
            if ($item['uuid'] === $candidate['uuid']) {
                if ($item['name'] !== $candidate['name']) {
                    throw new \RuntimeException('Backup set names cannot be changed');
                }
                foreach (['containerids', 'composeprojects'] as $property) {
                    if (!array_key_exists($property, $params)) {
                        $object->set($property, $item[$property] ?? '');
                    }
                }
            } else {
                $sets[] = $item;
            }
        }
        $forValidation = $object->getAssoc();
        if ($object->isNew()) {
            $forValidation['uuid'] = \OMV\Uuid::uuid4();
        }
        $sets[] = $forValidation;
        $this->validateCandidate($this->get([], $context), $sets);
        \OMV\Config\Database::getInstance()->set($object);
        return $object->getAssoc();
    }

    public function deleteSet($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.common.objectuuid');
        $this->command('check-idle');
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get('conf.service.protondrive.set', $params['uuid']);
        $db->delete($object);
        return $object->getAssoc();
    }

    public function getStatus($params, $context)
    {
        $this->admin($context);
        return $this->command('status');
    }

    public function getAuthStatus($params, $context)
    {
        $this->admin($context);
        return $this->command('auth-status');
    }

    public function startAuth($params, $context)
    {
        $this->admin($context);
        return $this->command('start-auth');
    }

    public function cancelAuth($params, $context)
    {
        $this->admin($context);
        return $this->command('cancel-auth');
    }

    public function logout($params, $context)
    {
        $this->admin($context);
        return $this->command('logout');
    }

    public function cancelRun($params, $context)
    {
        $this->admin($context);
        return $this->background('cancel-run');
    }

    public function recoverContainers($params, $context)
    {
        $this->admin($context);
        return $this->background('recover');
    }

    private function background($operation)
    {
        return $this->execBgProc(function ($statusFile, $outputFile) use ($operation) {
            $cmd = new \OMV\System\Process('/usr/sbin/omv-protondrive', [$operation]);
            $cmd->setRedirect2to1();
            if (0 !== ($exitCode = $this->exec($cmd, $output, $outputFile))) {
                throw new \OMV\ExecException($cmd, $output, $exitCode);
            }
            return $output;
        });
    }

    public function runNow($params, $context)
    {
        $this->admin($context);
        if ($this->isModuleDirty('protondrive')) {
            throw new \OMV\Config\ConfigDirtyException();
        }
        return $this->background('run-now');
    }
}
