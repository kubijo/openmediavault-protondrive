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

    private function storedJson($value)
    {
        return $value === '' || str_starts_with($value, 'json:') ? $value : 'json:' . $value;
    }

    private function exposedJson($value)
    {
        return str_starts_with($value, 'json:') ? substr($value, 5) : $value;
    }

    private function exposedSet($value)
    {
        $value['composeapps'] = $this->exposedJson($value['composeapps'] ?? '');
        return $value;
    }

    private function allSets()
    {
        $db = \OMV\Config\Database::getInstance();
        return array_map(function ($object) {
            return $this->exposedSet($object->getAssoc());
        }, $db->get('conf.service.protondrive.set'));
    }

    public function get($params, $context)
    {
        $this->admin($context);
        $settings = \OMV\Config\Database::getInstance()->get('conf.service.protondrive')->getAssoc();
        unset($settings['sets']);
        $settings['destinations'] = $this->exposedJson($settings['destinations'] ?? '');
        return $settings;
    }

    public function set($params, $context)
    {
        $this->admin($context);
        $current = $this->get([], $context);
        if (!array_key_exists('destinations', $params)) {
            $params['destinations'] = $current['destinations'] ?? '';
            if ($params['destinations'] !== '' && $params['remotepath'] !== $current['remotepath']) {
                $destinations = json_decode($params['destinations'], true, 512, JSON_THROW_ON_ERROR);
                foreach ($destinations as &$destination) {
                    if ($destination['kind'] === 'protondrive') {
                        $destination['root'] = $params['remotepath'];
                    }
                }
                unset($destination);
                $params['destinations'] = json_encode($destinations, JSON_THROW_ON_ERROR);
            }
        }
        $this->validateMethodParams($params, 'rpc.protondrive.set');
        $this->command('check-idle');
        if ($params['instanceuuid'] !== $current['instanceuuid']) {
            throw new \RuntimeException('The instance UUID cannot be changed');
        }
        $this->validateCandidate($params, $this->allSets());
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get('conf.service.protondrive');
        $stored = $params;
        $stored['destinations'] = $this->storedJson($params['destinations']);
        $object->setAssoc($stored);
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
        return $this->exposedSet(
            \OMV\Config\Database::getInstance()->get('conf.service.protondrive.set', $params['uuid'])->getAssoc(),
        );
    }

    public function setSet($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.protondrive.setSet');
        $this->command('check-idle');
        $object = new \OMV\Config\ConfigObject('conf.service.protondrive.set');
        $stored = $params;
        if (array_key_exists('composeapps', $stored)) {
            $stored['composeapps'] = $this->storedJson($stored['composeapps']);
        }
        $object->setAssoc($stored, true, true);
        $candidate = $object->getAssoc();
        $sets = [];
        foreach ($this->allSets() as $item) {
            if ($item['uuid'] === $candidate['uuid']) {
                if ($item['name'] !== $candidate['name']) {
                    throw new \RuntimeException('Backup set names cannot be changed');
                }
                foreach (['containerids', 'composeprojects', 'composeapps'] as $property) {
                    if (!array_key_exists($property, $params)) {
                        $value = $item[$property] ?? '';
                        $object->set($property, $property === 'composeapps' ? $this->storedJson($value) : $value);
                    }
                }
            } else {
                $sets[] = $item;
            }
        }
        $forValidation = $this->exposedSet($object->getAssoc());
        if ($object->isNew()) {
            $forValidation['uuid'] = \OMV\Uuid::uuid4();
        }
        $sets[] = $forValidation;
        $this->validateCandidate($this->get([], $context), $sets);
        \OMV\Config\Database::getInstance()->set($object);
        return $this->exposedSet($object->getAssoc());
    }

    public function deleteSet($params, $context)
    {
        $this->admin($context);
        $this->validateMethodParams($params, 'rpc.common.objectuuid');
        $this->command('check-idle');
        $db = \OMV\Config\Database::getInstance();
        $object = $db->get('conf.service.protondrive.set', $params['uuid']);
        $db->delete($object);
        return $this->exposedSet($object->getAssoc());
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
